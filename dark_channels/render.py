"""Montagem do vídeo longo (8-15 min) com FFmpeg, a partir da narração e das mídias.

Entra: o manifesto de narração que o voice.py gera (uma trilha por cena, com
duração medida) e um manifesto de mídias. Sai: um MP4 1920x1080 com áudio
normalizado.

Regra do time embutida aqui: **toda mídia precisa declarar `source` e
`license`**. A montagem recusa mídia sem isso, em vez de confiar na memória de
quem rodou o comando. É o que mantém o log de licenças honesto e a monetização
fora de risco.

Uso:
    python -m dark_channels.render --channel exemplo \
        --narracao data/exemplo/<roteiro>/narracao/manifesto.json \
        --midias data/exemplo/<roteiro>/midias.json \
        --saida data/exemplo/<roteiro>/video.mp4
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .voice import probe_duration, run_ffmpeg

LARGURA, ALTURA = 1920, 1080
FPS = 30

# Folga ao comparar a duração do vídeo com a do manifesto. Um frame a 30 fps
# são 33 ms; abaixo disso é arredondamento de contêiner, não fala cortada.
TOLERANCIA_SEGUNDOS = 1.0 / FPS

# Fontes de mídia que o time aceita. Material de terceiros fora desta lista não
# entra em vídeo nosso.
FONTES_ACEITAS = {
    "propria",        # gravada ou feita pelo time
    "gerada-local",   # gerada por IA na GPU da máquina
    "pexels",
    "pixabay",
    "youtube-audio-library",
    "licenciada",     # outra licença, registrada no campo `license`
}

EXTENSOES_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class MediaError(RuntimeError):
    """Mídia sem licença, sem fonte, ou com fonte não aceita."""


def validar_midia(item: dict, indice: int) -> dict:
    """Confere que a mídia pode entrar em vídeo nosso. Falha alto se não puder."""
    faltando = [c for c in ("path", "source", "license") if not item.get(c)]
    if faltando:
        raise MediaError(
            f"Mídia #{indice} sem {', '.join(faltando)}. Toda mídia precisa de "
            "caminho, fonte e licença registrada antes de entrar no vídeo."
        )
    fonte = str(item["source"]).lower()
    if fonte not in FONTES_ACEITAS:
        raise MediaError(
            f"Mídia #{indice} tem fonte '{item['source']}', que não está na lista "
            f"aceita ({', '.join(sorted(FONTES_ACEITAS))}). Material de terceiros "
            "sem licença não entra."
        )
    caminho = Path(item["path"])
    if not caminho.exists():
        raise MediaError(f"Mídia #{indice} não existe no disco: {caminho}")
    return item


def carregar_midias(path: str | Path) -> list[dict]:
    """Lê o manifesto de mídias e valida cada item.

    Formato:
        [{"path": "...", "source": "pexels", "license": "Pexels License",
          "credit": "Fulano", "url": "https://...", "scene": 0}, ...]
    """
    dados = json.loads(Path(path).read_text(encoding="utf-8"))
    itens = dados["media"] if isinstance(dados, dict) else dados
    if not itens:
        raise MediaError("Manifesto de mídias vazio.")
    return [validar_midia(dict(item), i) for i, item in enumerate(itens)]


def midias_por_cena(midias: list[dict], total_cenas: int) -> list[list[dict]]:
    """Distribui as mídias pelas cenas, de forma determinística.

    Mídia com `scene` vai para aquela cena. O resto é distribuído em ordem,
    dando a volta se houver menos mídias que cenas.
    """
    por_cena: list[list[dict]] = [[] for _ in range(total_cenas)]
    livres = []
    for item in midias:
        cena = item.get("scene")
        if cena is None:
            livres.append(item)
        elif 0 <= int(cena) < total_cenas:
            por_cena[int(cena)].append(item)
        else:
            raise MediaError(
                f"Mídia aponta para a cena {cena}, que não existe "
                f"(o roteiro tem {total_cenas})."
            )
    vazias = [i for i, m in enumerate(por_cena) if not m]
    if vazias and not livres:
        raise MediaError(
            f"Cenas sem mídia: {vazias}. Peça as imagens que faltam ao Diretor de Arte."
        )
    for posicao, indice in enumerate(vazias):
        por_cena[indice].append(livres[posicao % len(livres)])
    return por_cena


def e_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in EXTENSOES_VIDEO


def duracoes_da_linha_do_tempo(manifest: dict) -> list[float]:
    """Quanto tempo cada cena fica na tela, segundo a linha do tempo do áudio.

    Não é a duração da fala: entre uma cena e outra o voice.py insere uma
    pausa, e esse silêncio também precisa de imagem. Usar só
    ``cena["duration"]`` deixa o vídeo mais curto que a narração e corta a
    última frase — o manifesto é o contrato, e quem manda nele é o áudio.
    """
    cenas = manifest["scenes"]
    total = float(manifest.get("total_duration") or cenas[-1]["end"])
    duracoes = []
    for posicao, cena in enumerate(cenas):
        inicio = float(cena["start"])
        fim = float(cenas[posicao + 1]["start"]) if posicao + 1 < len(cenas) else total
        duracoes.append(round(max(fim - inicio, 1.0 / FPS), 3))
    return duracoes


def segmento_de_cena(midia: dict, duracao: float, saida: Path,
                     movimento: bool = True) -> Path:
    """Gera um trecho de vídeo mudo, do tamanho exato da narração da cena.

    Arredonda os frames para cima: é melhor sobrar um frame de imagem, que o
    ``-shortest`` da mixagem apara, do que faltar e cortar a voz.

    Quem decide o tamanho é ``-frames:v``, não ``-t`` na entrada. Com ``-t`` o
    ffmpeg limita a entrada antes, e uma duração que não cai em frame inteiro
    entrega um frame a menos do que foi pedido — foi o que encurtou a
    montagem em 0,34 s no primeiro teste.
    """
    origem = str(Path(midia["path"]).resolve())
    frames = max(math.ceil(duracao * FPS), 1)

    if e_video(origem):
        # Vídeo: dá a volta quantas vezes precisar; -frames:v corta no ponto.
        filtro = (
            f"scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,"
            f"crop={LARGURA}:{ALTURA},fps={FPS},format=yuv420p"
        )
        run_ffmpeg([
            "-stream_loop", "-1", "-i", origem,
            "-an", "-vf", filtro, "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(saida),
        ])
        return saida

    # Imagem fixa: um leve zoom (Ken Burns) evita tela morta.
    if movimento:
        filtro = (
            f"scale={LARGURA * 2}:{ALTURA * 2}:force_original_aspect_ratio=increase,"
            f"crop={LARGURA * 2}:{ALTURA * 2},"
            f"zoompan=z='min(1+0.0006*on,1.12)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={LARGURA}x{ALTURA}:fps={FPS},"
            f"format=yuv420p"
        )
    else:
        filtro = (
            f"scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,"
            f"crop={LARGURA}:{ALTURA},format=yuv420p"
        )
    run_ffmpeg([
        "-loop", "1", "-i", origem,
        "-an", "-vf", filtro, "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS), str(saida),
    ])
    return saida


def concatenar(segmentos: list[Path], saida: Path) -> Path:
    """Junta os trechos de cena em uma trilha de vídeo contínua."""
    lista = saida.with_suffix(".lista.txt")
    lista.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in segmentos),
        encoding="utf-8",
    )
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(lista),
        "-c", "copy", str(saida),
    ])
    lista.unlink(missing_ok=True)
    return saida


def juntar_audio_e_video(video: Path, narracao: Path, saida: Path,
                         musica: dict | None = None) -> Path:
    """Mixa narração (e música, se houver) e normaliza para publicação.

    A música entra baixa, 18 dB abaixo da narração, para não competir com a voz.
    Normalização final em -14 LUFS, que é o alvo do YouTube.
    """
    entradas = ["-i", str(video), "-i", str(narracao)]
    if musica:
        validar_midia(musica, 0)
        entradas += ["-stream_loop", "-1", "-i", str(Path(musica["path"]).resolve())]
        filtro = (
            "[2:a]volume=-18dB[mus];"
            "[1:a][mus]amix=inputs=2:duration=first:dropout_transition=0[mix];"
            "[mix]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )
    else:
        filtro = "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]"

    run_ffmpeg([
        *entradas, "-filter_complex", filtro,
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(saida),
    ])
    return saida


def render(narracao_manifest: str | Path, midias: str | Path, saida: str | Path,
           musica: dict | None = None, movimento: bool = True) -> dict:
    """Monta o vídeo longo. Devolve um relatório da montagem."""
    manifest_path = Path(narracao_manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cenas = manifest["scenes"]
    pasta_narracao = manifest_path.parent

    itens = carregar_midias(midias)
    distribuidas = midias_por_cena(itens, len(cenas))

    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    trabalho = saida.parent / "segmentos"
    trabalho.mkdir(parents=True, exist_ok=True)

    duracoes = duracoes_da_linha_do_tempo(manifest)
    segmentos = []
    for cena, midias_da_cena, duracao_na_tela in zip(cenas, distribuidas, duracoes):
        destino = trabalho / f"cena-{cena['index']:03d}.mp4"
        segmento_de_cena(
            midias_da_cena[0], duracao_na_tela, destino, movimento=movimento
        )
        segmentos.append(destino)

    video_mudo = trabalho / "video-mudo.mp4"
    concatenar(segmentos, video_mudo)

    narracao_audio = pasta_narracao / manifest.get("full_audio", "narracao.wav")
    juntar_audio_e_video(video_mudo, narracao_audio, saida, musica=musica)

    duracao = probe_duration(saida)
    esperado = float(manifest.get("total_duration") or cenas[-1]["end"])
    if duracao + TOLERANCIA_SEGUNDOS < esperado:
        raise RuntimeError(
            f"A montagem ficou em {duracao:.3f}s, abaixo dos {esperado:.3f}s de "
            "narração do manifesto: o fim da fala foi cortado. Confira as "
            "durações das cenas e as pausas entre elas."
        )
    usadas = [
        {k: v for k, v in m[0].items() if k in ("path", "source", "license", "credit", "url")}
        for m in distribuidas
    ]
    return {
        "saida": str(saida),
        "duracao_segundos": duracao,
        "duracao_minutos": round(duracao / 60, 2),
        "cenas": len(cenas),
        "midias": usadas,
        "musica": musica,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--channel")
    p.add_argument("--narracao", required=True, help="manifesto.json gerado pelo voice.py")
    p.add_argument("--midias", required=True, help="Manifesto de mídias com licenças")
    p.add_argument("--saida", required=True)
    p.add_argument("--musica", help="Manifesto JSON de uma trilha (com licença)")
    p.add_argument("--sem-movimento", action="store_true",
                   help="Desliga o zoom nas imagens (mais rápido, útil para rascunho)")
    a = p.parse_args()

    musica = json.loads(Path(a.musica).read_text(encoding="utf-8")) if a.musica else None
    relatorio = render(a.narracao, a.midias, a.saida, musica=musica,
                       movimento=not a.sem_movimento)
    print(f"Vídeo de {relatorio['duracao_minutos']:.1f} min em {relatorio['saida']}")
    if not 8 <= relatorio["duracao_minutos"] <= 15:
        print("Atenção: fora da faixa de 8 a 15 min combinada para o vídeo longo.")


if __name__ == "__main__":
    main()

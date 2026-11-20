"""Legendas dinâmicas a partir do manifesto de narração, e queima via FFmpeg.

Não usa transcrição: o manifesto do voice.py já traz o texto exato de cada cena
e a duração medida do áudio dela. Dentro da cena, o tempo é repartido entre os
grupos de palavras em proporção ao tamanho de cada grupo — a locução sintética
tem ritmo quase constante, então isso cai em cima da voz sem precisar de
reconhecimento de fala e sem erro de transcrição.

Dois estilos de legenda:
    horizontal  — vídeo longo 1920x1080, legenda discreta embaixo
    vertical    — Shorts/Reels/TikTok 1080x1920, letra grande, acima da faixa
                  onde os aplicativos desenham os botões

Uso:
    python -m dark_channels.subtitles --narracao .../narracao.json --saida legenda.ass
    python -m dark_channels.subtitles --narracao .../narracao.json \
        --queimar video.mp4 --saida video-legendado.mp4 --estilo vertical
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .voice import run_ffmpeg

# Grupos curtos: é o que dá o efeito "dinâmico" e o que se lê no celular.
PALAVRAS_POR_LEGENDA_VERTICAL = 3
PALAVRAS_POR_LEGENDA_HORIZONTAL = 7

ESTILOS = {
    # Vertical: letra grande e MarginV alto, para a legenda não cair atrás dos
    # botões de curtir/comentar/compartilhar dos aplicativos.
    "vertical": {
        "resolucao": (1080, 1920),
        "fonte": 84,
        "margem_v": 420,
        "contorno": 6,
        "palavras": PALAVRAS_POR_LEGENDA_VERTICAL,
    },
    "horizontal": {
        "resolucao": (1920, 1080),
        "fonte": 54,
        "margem_v": 90,
        "contorno": 4,
        "palavras": PALAVRAS_POR_LEGENDA_HORIZONTAL,
    },
}


def agrupar_palavras(texto: str, por_grupo: int) -> list[str]:
    """Divide o texto em grupos de no máximo ``por_grupo`` palavras."""
    palavras = texto.split()
    if not palavras:
        return []
    return [
        " ".join(palavras[i:i + por_grupo])
        for i in range(0, len(palavras), por_grupo)
    ]


def legendas_da_cena(cena: dict, por_grupo: int) -> list[dict]:
    """Reparte a duração da cena entre os grupos, proporcional ao tamanho."""
    grupos = agrupar_palavras(cena.get("narration", ""), por_grupo)
    if not grupos:
        return []
    pesos = [max(len(g), 1) for g in grupos]
    total = sum(pesos)
    duracao = float(cena["end"]) - float(cena["start"])
    cues = []
    tempo = float(cena["start"])
    for grupo, peso in zip(grupos, pesos):
        fatia = duracao * peso / total
        cues.append({
            "inicio": round(tempo, 3),
            "fim": round(tempo + fatia, 3),
            "texto": grupo,
        })
        tempo += fatia
    # Garante que a última legenda da cena termina exatamente no fim dela.
    cues[-1]["fim"] = round(float(cena["end"]), 3)
    return cues


def construir_cues(manifest: dict, por_grupo: int) -> list[dict]:
    cues: list[dict] = []
    for cena in manifest["scenes"]:
        cues.extend(legendas_da_cena(cena, por_grupo))
    return cues


def _tempo_srt(segundos: float) -> str:
    if segundos < 0:
        segundos = 0.0
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    milis = int(round((seg - int(seg)) * 1000))
    if milis == 1000:
        seg, milis = int(seg) + 1, 0
    return f"{int(horas):02d}:{int(minutos):02d}:{int(seg):02d},{milis:03d}"


def _tempo_ass(segundos: float) -> str:
    if segundos < 0:
        segundos = 0.0
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    centis = int(round((seg - int(seg)) * 100))
    if centis == 100:
        seg, centis = int(seg) + 1, 0
    return f"{int(horas):d}:{int(minutos):02d}:{int(seg):02d}.{centis:02d}"


def construir_srt(cues: list[dict]) -> str:
    linhas = []
    for numero, cue in enumerate(cues, start=1):
        linhas.append(str(numero))
        linhas.append(f"{_tempo_srt(cue['inicio'])} --> {_tempo_srt(cue['fim'])}")
        linhas.append(cue["texto"])
        linhas.append("")
    return "\n".join(linhas)


def construir_ass(cues: list[dict], estilo: str = "vertical") -> str:
    """Legenda em ASS, que permite controlar tamanho, contorno e posição."""
    if estilo not in ESTILOS:
        raise ValueError(f"Estilo '{estilo}' desconhecido; use vertical ou horizontal.")
    e = ESTILOS[estilo]
    largura, altura = e["resolucao"]
    cabecalho = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {largura}
PlayResY: {altura}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Padrao,Arial,{e['fonte']},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{e['contorno']},2,2,60,60,{e['margem_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    eventos = [
        f"Dialogue: 0,{_tempo_ass(c['inicio'])},{_tempo_ass(c['fim'])},"
        f"Padrao,,0,0,0,,{c['texto']}"
        for c in cues
    ]
    return cabecalho + "\n".join(eventos) + "\n"


def gerar(manifest_path: str | Path, saida: str | Path, estilo: str = "vertical",
          deslocamento: float = 0.0) -> Path:
    """Escreve o arquivo de legenda (.ass ou .srt, pela extensão da saída).

    ``deslocamento`` subtrai segundos de todos os tempos: serve para o Short,
    cujo corte começa no meio do vídeo longo.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    cues = construir_cues(manifest, ESTILOS[estilo]["palavras"])
    if deslocamento:
        cues = [
            {**c, "inicio": round(c["inicio"] - deslocamento, 3),
             "fim": round(c["fim"] - deslocamento, 3)}
            for c in cues
        ]
        cues = [c for c in cues if c["fim"] > 0]
        for c in cues:
            c["inicio"] = max(c["inicio"], 0.0)
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    texto = construir_srt(cues) if saida.suffix.lower() == ".srt" else construir_ass(cues, estilo)
    saida.write_text(texto, encoding="utf-8")
    return saida


def _escapar_filtro(path: Path) -> str:
    """Caminho de arquivo dentro de um filtro do ffmpeg, no formato que ele aceita.

    No Windows, `C:\\x` tem que virar `C\\:/x`, senão o ffmpeg lê os dois pontos
    como separador de opção do filtro.
    """
    texto = str(Path(path).resolve()).replace("\\", "/")
    return texto.replace(":", r"\:")


def queimar(video: str | Path, legenda: str | Path, saida: str | Path) -> Path:
    """Queima a legenda no vídeo (hardsub), que é o padrão em Shorts/Reels."""
    legenda = Path(legenda)
    filtro_nome = "ass" if legenda.suffix.lower() == ".ass" else "subtitles"
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(Path(video).resolve()),
        "-vf", f"{filtro_nome}='{_escapar_filtro(legenda)}'",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart", str(saida),
    ])
    return saida


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--narracao", required=True, help="narracao.json do voice.py")
    p.add_argument("--saida", required=True)
    p.add_argument("--estilo", choices=sorted(ESTILOS), default="horizontal")
    p.add_argument("--queimar", help="Vídeo em que a legenda será queimada")
    p.add_argument("--deslocamento", type=float, default=0.0)
    a = p.parse_args()

    if a.queimar:
        legenda = Path(a.saida).with_suffix(".ass")
        gerar(a.narracao, legenda, estilo=a.estilo, deslocamento=a.deslocamento)
        queimar(a.queimar, legenda, a.saida)
        print(f"Legenda queimada em {a.saida}")
        return

    gerar(a.narracao, a.saida, estilo=a.estilo, deslocamento=a.deslocamento)
    print(f"Legenda ({a.estilo}) salva em {a.saida}")


if __name__ == "__main__":
    main()

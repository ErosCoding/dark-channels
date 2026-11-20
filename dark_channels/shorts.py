"""Cortes verticais 9:16 para Shorts, Reels e TikTok, a partir do vídeo longo.

O roteiro já diz quais cenas formam cada Short (campo ``shorts``, com
``scene_indexes``), e o manifesto de narração diz em que segundo cada cena
começa e termina. Com os dois, a janela de corte é exata — nada de procurar o
trecho na mão.

Dois modos de enquadramento:
    cortar     — recorta as laterais (padrão; bom quando o assunto está no centro)
    enquadrar  — encaixa o vídeo inteiro sobre um fundo desfocado dele mesmo,
                 sem cortar nada. É o modo seguro quando há rosto ou texto perto
                 da borda.

Uso:
    python -m dark_channels.shorts --roteiro queue/exemplo/<arquivo>.json \
        --narracao data/.../manifesto.json --video data/.../video.mp4 \
        --pasta data/.../shorts
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

from . import subtitles
from .voice import probe_duration, run_ffmpeg

LARGURA, ALTURA = 1080, 1920
FPS = 30

# Limites das plataformas para o formato curto (YouTube Shorts aceita até 3 min,
# mas o que performa é bem mais curto).
DURACAO_MINIMA = 10.0
DURACAO_MAXIMA = 90.0


def slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")[:50] or "short"


def janela(manifest: dict, scene_indexes: list[int]) -> tuple[float, float]:
    """Começo e fim, em segundos, do trecho que cobre as cenas pedidas."""
    if not scene_indexes:
        raise ValueError("Short sem scene_indexes: não há como saber o trecho.")
    por_indice = {int(c["index"]): c for c in manifest["scenes"]}
    faltando = [i for i in scene_indexes if int(i) not in por_indice]
    if faltando:
        raise ValueError(
            f"O roteiro pede as cenas {faltando} para o Short, mas elas não foram "
            "narradas (confira o manifesto da narração)."
        )
    escolhidas = [por_indice[int(i)] for i in scene_indexes]
    return (
        round(min(float(c["start"]) for c in escolhidas), 3),
        round(max(float(c["end"]) for c in escolhidas), 3),
    )


def filtro_vertical(modo: str = "cortar", foco: float = 0.5) -> str:
    """Monta o filtro de conversão para 9:16.

    ``foco`` (0 a 1) diz de onde no quadro original vem o recorte: 0 é a borda
    esquerda, 0.5 o centro, 1 a borda direita. Serve para não cortar o rosto
    quando o assunto não está centrado.
    """
    if not 0.0 <= foco <= 1.0:
        raise ValueError("foco tem que estar entre 0 e 1.")
    if modo == "cortar":
        return (
            f"crop=w='min(iw,ih*9/16)':h=ih:x='(iw-min(iw,ih*9/16))*{foco}':y=0,"
            f"scale={LARGURA}:{ALTURA},setsar=1,fps={FPS}"
        )
    if modo == "enquadrar":
        # Fundo: o próprio vídeo ampliado e desfocado. Nada do quadro é perdido.
        return (
            f"split=2[fundo][frente];"
            f"[fundo]scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,"
            f"crop={LARGURA}:{ALTURA},gblur=sigma=28[bg];"
            f"[frente]scale={LARGURA}:-2:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}"
        )
    raise ValueError(f"Modo '{modo}' desconhecido; use cortar ou enquadrar.")


def cortar_vertical(video: str | Path, inicio: float, fim: float, saida: str | Path,
                    modo: str = "cortar", foco: float = 0.5) -> Path:
    """Recorta a janela do vídeo longo e entrega em 1080x1920."""
    duracao = round(fim - inicio, 3)
    if duracao <= 0:
        raise ValueError(f"Janela inválida: {inicio} a {fim}.")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    filtro = filtro_vertical(modo, foco)
    chave = "-filter_complex" if modo == "enquadrar" else "-vf"
    run_ffmpeg([
        "-ss", f"{inicio}", "-t", f"{duracao}", "-i", str(Path(video).resolve()),
        chave, filtro,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(saida),
    ])
    return saida


def montar_short(video: str | Path, manifest_path: str | Path, scene_indexes: list[int],
                 saida: str | Path, modo: str = "cortar", foco: float = 0.5,
                 legendar: bool = True) -> dict:
    """Corta, verticaliza e queima a legenda de um Short."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    inicio, fim = janela(manifest, scene_indexes)
    saida = Path(saida)
    bruto = saida.with_name(f"{saida.stem}-sem-legenda.mp4")
    cortar_vertical(video, inicio, fim, bruto, modo=modo, foco=foco)

    if legendar:
        legenda = saida.with_suffix(".ass")
        subtitles.gerar(manifest_path, legenda, estilo="vertical", deslocamento=inicio)
        subtitles.queimar(bruto, legenda, saida)
        bruto.unlink(missing_ok=True)
    else:
        bruto.replace(saida)

    duracao = probe_duration(saida)
    aviso = None
    if duracao < DURACAO_MINIMA:
        aviso = f"curto demais ({duracao:.1f}s) para render bem como Short"
    elif duracao > DURACAO_MAXIMA:
        aviso = f"longo demais ({duracao:.1f}s); o formato curto rende mais abaixo de 90s"
    return {
        "saida": str(saida),
        "inicio": inicio,
        "fim": fim,
        "duracao": duracao,
        "cenas": list(scene_indexes),
        "modo": modo,
        "aviso": aviso,
    }


def montar_todos(roteiro_path: str | Path, manifest_path: str | Path,
                 video: str | Path, pasta: str | Path, modo: str = "cortar",
                 foco: float = 0.5) -> list[dict]:
    """Monta todos os Shorts que o roteiro aprovado pediu."""
    payload = json.loads(Path(roteiro_path).read_text(encoding="utf-8"))
    if payload.get("status") != "approved":
        raise RuntimeError(
            f"Roteiro com status '{payload.get('status')}'. A produção só começa "
            "depois da aprovação humana."
        )
    pedidos = payload["script"].get("shorts") or []
    if not pedidos:
        raise RuntimeError("O roteiro não indicou nenhum Short (campo 'shorts' vazio).")
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    relatorios = []
    for numero, pedido in enumerate(pedidos, start=1):
        destino = pasta / f"short-{numero:02d}-{slug(pedido.get('title', ''))}.mp4"
        relatorio = montar_short(
            video, manifest_path, pedido.get("scene_indexes", []), destino,
            modo=modo, foco=foco,
        )
        relatorio["titulo"] = pedido.get("title", "")
        relatorios.append(relatorio)
    return relatorios


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--roteiro", required=True, help="Roteiro aprovado da fila")
    p.add_argument("--narracao", required=True, help="manifesto.json do voice.py")
    p.add_argument("--video", required=True, help="Vídeo longo já montado")
    p.add_argument("--pasta", required=True, help="Pasta de saída dos Shorts")
    p.add_argument("--modo", choices=("cortar", "enquadrar"), default="cortar")
    p.add_argument("--foco", type=float, default=0.5,
                   help="0 a 1: de onde vem o recorte horizontal (0.5 = centro)")
    a = p.parse_args()

    for relatorio in montar_todos(a.roteiro, a.narracao, a.video, a.pasta,
                                  modo=a.modo, foco=a.foco):
        linha = f"{relatorio['duracao']:.1f}s -> {relatorio['saida']}"
        if relatorio["aviso"]:
            linha += f"  (atenção: {relatorio['aviso']})"
        print(linha)


if __name__ == "__main__":
    main()

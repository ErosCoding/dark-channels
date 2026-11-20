"""Narração em PT-BR com o VoiceStudio local (custo US$ 0, nenhuma chave paga).

O VoiceStudio roda inteiro na máquina e expõe um endpoint compatível com a API
da OpenAI em ``POST /v1/audio/speech``. Nada é enviado para fora.

Gera **um arquivo de áudio por cena** do roteiro, não um arquivo único. É isso
que permite ao render.py saber a duração exata de cada cena e sincronizar
visuais e legendas sem adivinhar.

Uso:
    python -m dark_channels.voice --channel exemplo --script queue/exemplo/<arquivo>.json
    python -m dark_channels.voice --texto "Teste de narração" --saida data/amostra.wav
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .config import DATA_DIR, load_channel

# Limite do campo `input` do endpoint /v1/audio/speech (OpenAI-compatible).
MAX_INPUT_CHARS = 4096

# Padrões de voz, se o config.yaml do canal não disser nada.
DEFAULT_VOICE = {
    "engine": "omnivoice",
    "name": "default",
    "speed": 1.0,
    "language": "pt",
    "pause_between_scenes_ms": 450,
    "format": "wav",
}


def base_url() -> str:
    """Endereço do VoiceStudio local. Loopback não precisa de credencial."""
    explicit = os.environ.get("VOICESTUDIO_URL")
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("OMNIVOICE_PORT", "3900")
    return f"http://127.0.0.1:{port}"


def voice_settings(channel_cfg: dict | None = None) -> dict:
    cfg = dict(DEFAULT_VOICE)
    if channel_cfg:
        cfg.update(channel_cfg.get("voice") or {})
    return cfg


def _request(path: str, payload: dict, timeout: float) -> bytes:
    url = f"{base_url()}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    key = os.environ.get("OMNIVOICE_API_KEY")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(
            f"VoiceStudio respondeu {exc.code} em {path}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"VoiceStudio não respondeu em {base_url()} ({exc.reason}). "
            "Abra o aplicativo VoiceStudio para subir o backend local, ou "
            "defina VOICESTUDIO_URL."
        ) from exc


def is_up(timeout: float = 5.0) -> bool:
    """True se o backend local está de pé."""
    url = f"{base_url()}/.well-known/voicestudio-speech"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except OSError:
        return False


def split_text(text: str, limit: int = MAX_INPUT_CHARS) -> list[str]:
    """Divide o texto em blocos abaixo do limite do endpoint, sem cortar frase.

    Quebra em fim de frase; se uma frase sozinha passar do limite, quebra em
    espaço. Nunca devolve bloco vazio.
    """
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    sentences = re.split(r"(?<=[.!?…:;])\s+", text)
    blocks: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > limit:
            # Frase gigante: corta no último espaço que ainda cabe.
            cut = sentence.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            head, sentence = sentence[:cut].strip(), sentence[cut:].strip()
            if current:
                blocks.append(current)
                current = ""
            blocks.append(head)
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                blocks.append(current)
            current = sentence
    if current:
        blocks.append(current)
    return [b for b in blocks if b]


def synthesize(text: str, out_path: str | Path, voice: dict | None = None,
               timeout: float = 600.0) -> Path:
    """Sintetiza ``text`` e grava em ``out_path``. Divide em blocos se preciso."""
    voice = voice_settings() if voice is None else voice
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = split_text(text)
    if not blocks:
        raise ValueError("Texto vazio: nada para narrar.")

    fmt = voice.get("format", "wav")
    parts: list[Path] = []
    for index, block in enumerate(blocks):
        payload = {
            "model": voice.get("engine", "omnivoice"),
            "input": block,
            "voice": voice.get("name", "default"),
            "response_format": fmt,
            "speed": float(voice.get("speed", 1.0)),
            "language": voice.get("language", "pt"),
        }
        if voice.get("instruct"):
            payload["instruct"] = voice["instruct"]
        audio = _request("/v1/audio/speech", payload, timeout)
        if not audio:
            raise RuntimeError("VoiceStudio devolveu áudio vazio.")
        if len(blocks) == 1:
            out_path.write_bytes(audio)
            return out_path
        part = out_path.with_name(f"{out_path.stem}.parte{index:02d}{out_path.suffix}")
        part.write_bytes(audio)
        parts.append(part)

    concat_audio(parts, out_path)
    for part in parts:
        part.unlink(missing_ok=True)
    return out_path


def concat_audio(parts: list[Path], out_path: Path) -> Path:
    """Junta arquivos de áudio com ffmpeg, recodificando para evitar emenda torta."""
    if not parts:
        raise ValueError("Nenhuma parte de áudio para juntar.")
    listing = out_path.with_suffix(".lista.txt")
    listing.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8"
    )
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c:a", "pcm_s16le", "-ar", "24000", "-ac", "1", str(out_path),
    ])
    listing.unlink(missing_ok=True)
    return out_path


def run_ffmpeg(args: list[str]) -> None:
    """Roda ffmpeg sobrescrevendo a saída, e falha com a mensagem real do ffmpeg."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg falhou ({result.returncode}): {result.stderr.strip()[:800]}"
        )


def probe_duration(path: str | Path) -> float:
    """Duração em segundos, medida com ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe falhou em {path}: {result.stderr.strip()[:400]}")
    return round(float(result.stdout.strip()), 3)


def load_approved_script(path: str | Path) -> dict:
    """Lê o roteiro da fila e recusa o que não foi aprovado por um humano."""
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    status = payload.get("status")
    if status != "approved":
        raise RuntimeError(
            f"Roteiro {path.name} está com status '{status}', não 'approved'. "
            "A produção só começa depois da aprovação humana."
        )
    return payload


def narrate_script(script: dict, out_dir: str | Path, voice: dict | None = None,
                   timeout: float = 600.0) -> dict:
    """Narra cada cena do roteiro e devolve um manifesto com as durações.

    O manifesto é o contrato que o render.py consome.
    """
    voice = voice_settings() if voice is None else voice
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scenes = script.get("scenes") or []
    if not scenes:
        raise ValueError("Roteiro sem cenas.")

    fmt = voice.get("format", "wav")
    entries = []
    start = 0.0
    pause = float(voice.get("pause_between_scenes_ms", 0)) / 1000.0
    for index, scene in enumerate(scenes):
        narration = (scene.get("narration") or "").strip()
        if not narration:
            continue
        audio_path = out_dir / f"cena-{index:03d}.{fmt}"
        synthesize(narration, audio_path, voice=voice, timeout=timeout)
        duration = probe_duration(audio_path)
        entries.append({
            "index": index,
            "audio": audio_path.name,
            "narration": narration,
            "visual": scene.get("visual", ""),
            "duration": duration,
            "start": round(start, 3),
            "end": round(start + duration, 3),
        })
        start += duration + pause

    if not entries:
        raise ValueError("Nenhuma cena tinha narração.")

    manifest = {
        "engine": voice.get("engine", "omnivoice"),
        "voice": voice.get("name", "default"),
        "language": voice.get("language", "pt"),
        "speed": float(voice.get("speed", 1.0)),
        "pause_between_scenes_ms": voice.get("pause_between_scenes_ms", 0),
        "format": fmt,
        "total_duration": round(start - pause if pause else start, 3),
        "scenes": entries,
    }
    manifest_path = out_dir / "narracao.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    full = out_dir / f"narracao.{fmt}"
    concat_with_pauses([out_dir / e["audio"] for e in entries], full, pause)
    manifest["full_audio"] = full.name
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def concat_with_pauses(parts: list[Path], out_path: Path, pause_seconds: float) -> Path:
    """Junta as cenas inserindo silêncio entre elas."""
    if pause_seconds <= 0 or len(parts) == 1:
        return concat_audio(parts, out_path)
    silence = out_path.with_name("silencio.wav")
    run_ffmpeg([
        "-f", "lavfi", "-i",
        "anullsrc=channel_layout=mono:sample_rate=24000",
        "-t", f"{pause_seconds}", "-c:a", "pcm_s16le", str(silence),
    ])
    interleaved: list[Path] = []
    for i, part in enumerate(parts):
        if i:
            interleaved.append(silence)
        interleaved.append(part)
    concat_audio(interleaved, out_path)
    silence.unlink(missing_ok=True)
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--channel", help="Canal, para ler a voz do config.yaml")
    p.add_argument("--script", help="Roteiro aprovado em queue/<canal>/*.json")
    p.add_argument("--texto", help="Narra um texto solto (teste rápido)")
    p.add_argument("--saida", help="Arquivo de saída, com --texto")
    p.add_argument("--timeout", type=float, default=600.0)
    a = p.parse_args()

    cfg = load_channel(a.channel) if a.channel else None
    voice = voice_settings(cfg)

    if not is_up():
        raise SystemExit(
            f"VoiceStudio não está respondendo em {base_url()}. "
            "Abra o aplicativo VoiceStudio (ele sobe o backend local) e repita."
        )

    if a.texto:
        out = Path(a.saida or DATA_DIR / "amostra-narracao.wav")
        synthesize(a.texto, out, voice=voice, timeout=a.timeout)
        print(f"Narração de {probe_duration(out):.1f}s salva em {out}")
        return

    if not (a.script and a.channel):
        raise SystemExit("Use --texto --saida, ou --channel com --script.")

    payload = load_approved_script(a.script)
    out_dir = DATA_DIR / a.channel / Path(a.script).stem / "narracao"
    manifest = narrate_script(payload["script"], out_dir, voice=voice, timeout=a.timeout)
    minutes = manifest["total_duration"] / 60
    print(f"{len(manifest['scenes'])} cenas narradas, {minutes:.1f} min no total.")
    print(f"Manifesto: {out_dir / 'narracao.json'}")


if __name__ == "__main__":
    main()

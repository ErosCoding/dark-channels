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
import urllib.parse
import urllib.request
from pathlib import Path

from .config import DATA_DIR, load_channel

# Limite do campo `input` do endpoint /v1/audio/speech (OpenAI-compatible).
MAX_INPUT_CHARS = 4096

# Diferença aceitável entre a narração medida e a somada. Acima disso a
# emenda saiu errada e a montagem cortaria fala.
TOLERANCIA_EMENDA_SEGUNDOS = 0.05

# Motores cujo modelo tem licença NÃO COMERCIAL. Narrar um vídeo monetizado
# com qualquer um deles é violação de licença, então a narração é recusada
# antes de sair qualquer requisição — não basta o config.yaml estar certo,
# porque um canal novo pode simplesmente não trazer o campo `engine`.
#
# Conferido em 20/11/2026 com `voicestudio models license show k2-fsa/OmniVoice`:
# "CC-BY-NC (version unspecified); Higgs Audio 2; Meta Llama 3",
# license_category: noncommercial, commercial_inference: restricted.
MOTORES_NAO_COMERCIAIS = frozenset({
    "omnivoice",
    "omnivoice-subprocess",
    "omnivoice-gguf",
})

# ARMADILHA, para quem for conferir licença no VoiceStudio: o registro interno
# dele em `backend/config/model_licenses.json` marca "commercial_use": false
# para o Supertonic. Isso NÃO é proibição da licença. No mesmo registro
# `review_status` é "unreviewed", e a auditoria do próprio aplicativo diz que
# "commercial use is not categorically prohibited" — ou seja, o false ali quer
# dizer "não revisado", não "proibido".
#
# O parecer do Monetização e Compliance de 20/11/2026 liberou o Supertonic-3
# para uso comercial: pesos em BigScience Open RAIL-M, concessão irrevogável e
# sem royalties, e a Seção 6 é literal — "Licensor claims no rights in the
# Output You generate using the Model". O áudio é nosso.
# Atribuição não é exigida, e a Seção 8 PROÍBE sugerir endosso: nunca escreva
# algo como "narração oficial Supertone".
# Parecer completo: Shared\2026-11-20-parecer-licenca-supertonic-3-narracao.md

# Padrões de voz, se o config.yaml do canal não disser nada. O padrão é um
# motor de licença comercial de propósito: o padrão do VoiceStudio é o
# omnivoice, que não serve para canal monetizado.
DEFAULT_VOICE = {
    "engine": "supertonic3",  # Supertone/supertonic-3: MIT + OpenRAIL-M
    "name": "M1",
    "speed": 1.0,
    "language": "pt",
    "pause_between_scenes_ms": 450,
    "format": "wav",
}


class LicencaNaoComercialError(RuntimeError):
    """Pedido de narração com um motor que não pode ser usado comercialmente."""


def resolver_motor(voice: dict | None = None) -> str:
    """Devolve o motor a usar, recusando os de licença não comercial.

    É a última linha de defesa: roda em toda síntese, mesmo que o config.yaml
    do canal não traga `engine`.
    """
    voice = voice or {}
    engine = str(voice.get("engine") or DEFAULT_VOICE["engine"]).strip().lower()
    if engine in MOTORES_NAO_COMERCIAIS:
        raise LicencaNaoComercialError(
            f"O motor de voz '{engine}' usa um modelo de licença não comercial "
            "(CC-BY-NC) e não pode narrar vídeo de canal monetizado. "
            f"Escolha um motor com licença comercial no config.yaml do canal "
            f"(padrão atual: {DEFAULT_VOICE['engine']}) e confira em "
            "`voicestudio models license show <repo_id>`."
        )
    return engine


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _registro_publicado() -> str | None:
    """Endereço que o aplicativo publica em backend.json, se houver.

    O VoiceStudio nem sempre usa a porta 3900: cada perfil escolhe a sua e
    anota o endereço nesse arquivo. Só aceitamos registro local — um registro
    apontando para fora da máquina seria mandar o texto do roteiro para a rede.
    """
    base = os.environ.get("VOICESTUDIO_USER_DATA")
    pasta = Path(base).expanduser() if base else Path(
        os.environ.get("APPDATA", Path.home() / "AppData/Roaming")
    ) / "VoiceStudio"
    try:
        registro = json.loads((pasta / "backend.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(registro, dict) or registro.get("schema") != 1:
        return None
    url = registro.get("url")
    if not isinstance(url, str) or registro.get("remote"):
        return None
    partes = urllib.parse.urlsplit(url)
    if partes.scheme != "http" or partes.hostname not in LOOPBACK:
        return None
    return url.rstrip("/")


def base_url() -> str:
    """Endereço do VoiceStudio local. Loopback não precisa de credencial."""
    explicit = os.environ.get("VOICESTUDIO_URL")
    if explicit:
        return explicit.rstrip("/")
    publicado = _registro_publicado()
    if publicado:
        return publicado
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

    engine = resolver_motor(voice)  # recusa motor de licença não comercial
    fmt = voice.get("format", "wav")
    parts: list[Path] = []
    for index, block in enumerate(blocks):
        payload = {
            "model": engine,
            "input": block,
            "voice": voice.get("name") or DEFAULT_VOICE["name"],
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


def concat_audio(parts: list[Path], out_path: Path,
                 sample_rate: int | None = None) -> Path:
    """Junta arquivos de áudio com ffmpeg, recodificando para evitar emenda torta.

    A taxa de amostragem sai do primeiro arquivo, não de um número fixo: cada
    motor de voz entrega a sua (o Supertonic-3 dá 44,1 kHz) e o demuxer concat
    toca tudo na taxa do primeiro trecho. Fixar 24 kHz aqui fazia um silêncio
    de 450 ms virar 245 ms no meio de uma narração de 44,1 kHz.
    """
    if not parts:
        raise ValueError("Nenhuma parte de áudio para juntar.")
    if sample_rate is None:
        sample_rate = probe_sample_rate(parts[0])
    listing = out_path.with_suffix(".lista.txt")
    listing.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8"
    )
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1", str(out_path),
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


def probe_sample_rate(path: str | Path) -> int:
    """Taxa de amostragem do arquivo, medida com ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"ffprobe não leu a taxa de amostragem de {path}: "
            f"{result.stderr.strip()[:400]}"
        )
    return int(result.stdout.strip().splitlines()[0])


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
    engine = resolver_motor(voice)  # recusa antes de gerar qualquer cena
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

    previsto = round(start - pause if pause else start, 3)

    full = out_dir / f"narracao.{fmt}"
    concat_with_pauses([out_dir / e["audio"] for e in entries], full, pause)
    medido = probe_duration(full)

    # total_duration é a duração MEDIDA do arquivo, não a soma prevista. O
    # render.py monta a imagem em cima deste número, então ele precisa
    # descrever o arquivo que existe. Uma divergência grande aqui é sinal de
    # emenda errada (taxa de amostragem diferente, por exemplo) e não pode
    # passar calada até virar fala cortada no vídeo.
    if abs(medido - previsto) > TOLERANCIA_EMENDA_SEGUNDOS:
        raise RuntimeError(
            f"A narração juntada tem {medido:.3f}s, mas as cenas e as pausas "
            f"somam {previsto:.3f}s. A emenda do áudio saiu errada; confira a "
            "taxa de amostragem das cenas e do silêncio."
        )

    manifest = {
        "engine": engine,
        "voice": voice.get("name") or DEFAULT_VOICE["name"],
        "language": voice.get("language", "pt"),
        "speed": float(voice.get("speed", 1.0)),
        "pause_between_scenes_ms": voice.get("pause_between_scenes_ms", 0),
        "format": fmt,
        "sample_rate": probe_sample_rate(full),
        "total_duration": medido,
        "total_duration_previsto": previsto,
        "full_audio": full.name,
        "scenes": entries,
    }
    # O manifesto NÃO se chama narracao.json de propósito: o transcrever.py do
    # stack de edição grava <nome-do-audio>.json ao lado do áudio, e transcrever
    # narracao.wav sobrescreveria o contrato silenciosamente. Aconteceu aqui em
    # 20/11/2026.
    manifest_path = out_dir / "manifesto.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def concat_with_pauses(parts: list[Path], out_path: Path, pause_seconds: float) -> Path:
    """Junta as cenas inserindo silêncio entre elas.

    O silêncio é gerado na mesma taxa de amostragem das cenas. Gerar em outra
    taxa não dá erro: o demuxer concat simplesmente toca o silêncio na taxa da
    primeira faixa, e a pausa encurta na proporção das duas taxas.
    """
    if pause_seconds <= 0 or len(parts) == 1:
        return concat_audio(parts, out_path)
    sample_rate = probe_sample_rate(parts[0])
    silence = out_path.with_name("silencio.wav")
    run_ffmpeg([
        "-f", "lavfi", "-i",
        f"anullsrc=channel_layout=mono:sample_rate={sample_rate}",
        "-t", f"{pause_seconds}", "-c:a", "pcm_s16le", str(silence),
    ])
    interleaved: list[Path] = []
    for i, part in enumerate(parts):
        if i:
            interleaved.append(silence)
        interleaved.append(part)
    concat_audio(interleaved, out_path, sample_rate=sample_rate)
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
    print(f"Manifesto: {out_dir / 'manifesto.json'}")


if __name__ == "__main__":
    main()

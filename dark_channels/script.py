"""Gera um roteiro com Claude e o coloca na fila de aprovação (queue/<canal>/)."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime

import anthropic
from pydantic import BaseModel, Field

from .config import QUEUE_DIR, load_channel, load_prompt


class Scene(BaseModel):
    narration: str = Field(description="Texto narrado nesta cena")
    visual: str = Field(description="O que aparece na tela, ilustrando a narração")


class ShortCut(BaseModel):
    title: str
    scene_indexes: list[int] = Field(description="Índices das cenas (base 0) que formam o Short")


class Script(BaseModel):
    topic: str
    angle: str = Field(description="O ângulo/tese próprio deste vídeo, em uma frase")
    hook: str = Field(description="Os primeiros 15 segundos de narração")
    scenes: list[Scene]
    titles: list[str]
    thumbnail_ideas: list[str]
    description: str = Field(description="Descrição do vídeo para o YouTube, com fontes ao final")
    tags: list[str]
    sources: list[str] = Field(description="Fontes que embasam os fatos do roteiro")
    shorts: list[ShortCut]


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def word_count(script: Script) -> int:
    return sum(len(s.narration.split()) for s in script.scenes)


def generate(channel: str, topic: str) -> Script:
    cfg = load_channel(channel)
    target_minutes = cfg["video"]["target_minutes"]
    prompt = load_prompt(channel, "script").format(
        niche=cfg["niche"], audience=cfg["audience"], tone=cfg["tone"],
        language=cfg["language"], topic=topic, target_minutes=target_minutes,
        target_words=target_minutes * cfg["video"]["words_per_minute"],
    )

    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=cfg["claude"]["model"],
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": cfg["claude"].get("effort", "high")},
        messages=[{"role": "user", "content": prompt}],
        output_format=Script,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude recusou o tema '{topic}'. Escolha outro tema.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Roteiro cortado por limite de tokens; reduza target_minutes.")
    return response.parsed_output


def enqueue(channel: str, script: Script) -> str:
    folder = QUEUE_DIR / channel
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d-%H%M}-{slugify(script.topic)}.json"
    payload = {
        "status": "pending",  # mude para "approved" depois de revisar
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "words": word_count(script),
        "script": script.model_dump(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--channel", required=True)
    p.add_argument("--topic", required=True)
    a = p.parse_args()
    script = generate(a.channel, a.topic)
    path = enqueue(a.channel, script)
    print(f"Roteiro ({word_count(script)} palavras, {len(script.scenes)} cenas) salvo em {path}")
    print("Revise e mude status para 'approved' para liberar a produção.")


if __name__ == "__main__":
    main()

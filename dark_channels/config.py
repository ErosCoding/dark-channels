"""Carrega a configuração de um canal e o .env."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CHANNELS_DIR = ROOT / "channels"
QUEUE_DIR = ROOT / "queue"
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env")


def load_channel(name: str) -> dict:
    path = CHANNELS_DIR / name / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Canal '{name}' não encontrado em {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompt(channel: str, prompt: str) -> str:
    return (CHANNELS_DIR / channel / "prompts" / f"{prompt}.md").read_text(encoding="utf-8")


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variável de ambiente {name} não definida (veja .env.example)")
    return value

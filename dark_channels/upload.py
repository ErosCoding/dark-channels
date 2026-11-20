"""Envia um vídeo pronto ao YouTube, agendado, com o selo de conteúdo sintético.

Só aceita roteiros com status "approved" na fila. Cada upload custa ~1.600
unidades de cota (cerca de 6 por dia na cota grátis).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from googleapiclient.http import MediaFileUpload

from .config import load_channel
from .youtube_auth import youtube


def build_body(cfg: dict, script: dict, publish_at: datetime | None, title_index: int = 0) -> dict:
    tags = list(dict.fromkeys(script["tags"] + cfg["youtube"].get("default_tags", [])))
    status = {
        "privacyStatus": "private" if publish_at else "public",
        "selfDeclaredMadeForKids": False,
        # Declara uso de IA (narração/imagens sintéticas). Obrigatório pela política do YouTube.
        "containsSyntheticMedia": True,
    }
    if publish_at:
        if publish_at.tzinfo is None:
            raise ValueError("--publish-at precisa de fuso horário, ex.: 2026-11-20T18:00:00-03:00")
        status["publishAt"] = publish_at.isoformat()
    return {
        "snippet": {
            "title": script["titles"][title_index][:100],
            "description": script["description"][:5000],
            "tags": tags[:30],
            "categoryId": cfg["youtube"]["category_id"],
            "defaultLanguage": cfg["language"],
            "defaultAudioLanguage": cfg["language"],
        },
        "status": status,
    }


def upload(channel: str, video: str, script_path: str, publish_at: datetime | None,
           title_index: int = 0) -> str:
    entry = json.loads(Path(script_path).read_text(encoding="utf-8"))
    if entry.get("status") != "approved":
        raise RuntimeError(f"Roteiro {script_path} ainda não foi aprovado (status={entry.get('status')})")

    body = build_body(load_channel(channel), entry["script"], publish_at, title_index)
    request = youtube().videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(video, chunksize=-1, resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()

    entry.update(status="uploaded", video_id=response["id"],
                 publish_at=publish_at.isoformat() if publish_at else None)
    Path(script_path).write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return response["id"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--channel", required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--script", required=True, help="Arquivo JSON da fila (aprovado)")
    p.add_argument("--publish-at", type=datetime.fromisoformat, help="ISO 8601 com fuso")
    p.add_argument("--title-index", type=int, default=0)
    a = p.parse_args()
    vid = upload(a.channel, a.video, a.script, a.publish_at, a.title_index)
    print(f"Enviado: https://youtu.be/{vid}")


if __name__ == "__main__":
    main()

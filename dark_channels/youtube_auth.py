"""Autenticação OAuth do canal.

Rode uma vez no seu computador para gerar o refresh token:

    python -m dark_channels.youtube_auth

Pré-requisito: no Google Cloud, crie credenciais OAuth do tipo "App para computador"
e preencha YOUTUBE_CLIENT_ID e YOUTUBE_CLIENT_SECRET no .env. Faça login com a conta
dona do canal. Copie o refresh token exibido para YOUTUBE_REFRESH_TOKEN.
"""

from __future__ import annotations

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import require_env

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=require_env("YOUTUBE_REFRESH_TOKEN"),
        client_id=require_env("YOUTUBE_CLIENT_ID"),
        client_secret=require_env("YOUTUBE_CLIENT_SECRET"),
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )


def youtube():
    return build("youtube", "v3", credentials=credentials())


def main() -> None:
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": require_env("YOUTUBE_CLIENT_ID"),
            "client_secret": require_env("YOUTUBE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
        }},
        SCOPES,
    )
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    print("\nYOUTUBE_REFRESH_TOKEN=" + creds.refresh_token)


if __name__ == "__main__":
    main()

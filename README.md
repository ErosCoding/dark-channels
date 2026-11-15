# dark-channels

Pipeline para criar e operar canais dark (sem rosto) no YouTube: pesquisa de nicho com dados, roteiro com Claude, narração, montagem, upload agendado e acompanhamento de métricas.

Plano completo: [Plano: Canais Dark no YouTube](https://claude.ai/artifact/EAZdozNa1D1ibvQu5zNXRB)

## Status

| Etapa | Módulo | Status |
| --- | --- | --- |
| Pesquisa de outliers | `dark_channels/research.py` | pronto |
| Roteiro com Claude | `dark_channels/script.py` | pronto |
| Upload agendado | `dark_channels/upload.py` | pronto |
| Narração (ElevenLabs) | `dark_channels/voice.py` | a fazer |
| Visuais + montagem (FFmpeg) | `dark_channels/render.py` | a fazer |
| Thumbnail | `dark_channels/thumbnail.py` | a fazer |
| Métricas e relatório semanal | `dark_channels/analytics.py` | a fazer |

## Estrutura

```
channels/<canal>/config.yaml   # nicho, idioma, voz, cadência
channels/<canal>/prompts/      # prompts do canal
dark_channels/                 # código do pipeline
queue/<canal>/                 # roteiros aguardando sua aprovação
data/                          # saídas de pesquisa e métricas (não versionado)
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha as chaves
```

Chaves necessárias (no `.env` local ou em *Settings → Secrets* do GitHub):

- `ANTHROPIC_API_KEY`: [console.anthropic.com](https://console.anthropic.com)
- `YOUTUBE_API_KEY`: Google Cloud → APIs → YouTube Data API v3 → Credenciais → Chave de API (usada só na pesquisa)
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`: OAuth do canal, para upload (gere o refresh token com `python -m dark_channels.youtube_auth`)

## Uso

```bash
# 1. Achar outliers (vídeos que performaram muito acima do tamanho do canal)
python -m dark_channels.research --keywords "história do brasil" "mistérios reais" --days 90

# 2. Gerar um roteiro para a fila de aprovação
python -m dark_channels.script --channel exemplo --topic "O mistério do voo 914"

# 3. Aprovar: mude "status: pending" para "status: approved" no arquivo em queue/exemplo/

# 4. Enviar um vídeo pronto, agendado
python -m dark_channels.upload --channel exemplo --video out/video.mp4 --script queue/exemplo/<arquivo>.json --publish-at 2026-11-20T18:00:00-03:00
```

## Regras para não perder a monetização

- Roteiro original por vídeo, sempre aprovado por um humano antes de publicar.
- Nada de clipes de terceiros. Só mídia própria, gerada ou com licença registrada.
- Todo upload marca `containsSyntheticMedia` (selo de IA do YouTube).
- Volume moderado: nada de dezenas de vídeos quase iguais por dia.

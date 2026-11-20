# dark-channels

Pipeline para criar e operar canais dark (sem rosto) no YouTube: pesquisa de nicho com dados, roteiro com Claude, narração, montagem, upload agendado e acompanhamento de métricas.

Plano completo: [Plano: Canais Dark no YouTube](https://claude.ai/artifact/EAZdozNa1D1ibvQu5zNXRB)

## Status

| Etapa | Módulo | Status |
| --- | --- | --- |
| Pesquisa de outliers | `dark_channels/research.py` | pronto |
| Roteiro com Claude | `dark_channels/script.py` | pronto |
| Upload agendado | `dark_channels/upload.py` | pronto |
| Narração (VoiceStudio local) | `dark_channels/voice.py` | pronto |
| Visuais + montagem (FFmpeg) | `dark_channels/render.py` | pronto |
| Legendas dinâmicas | `dark_channels/subtitles.py` | pronto |
| Cortes verticais 9:16 | `dark_channels/shorts.py` | pronto |
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
  (só para `script.py`; a narração **não** usa chave paga)
- `YOUTUBE_API_KEY`: Google Cloud → APIs → YouTube Data API v3 → Credenciais → Chave de API (usada só na pesquisa)
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`: OAuth do canal, para upload (gere o refresh token com `python -m dark_channels.youtube_auth`)

## Uso

```bash
# 1. Achar outliers (vídeos que performaram muito acima do tamanho do canal)
python -m dark_channels.research --keywords "história do brasil" "mistérios reais" --days 90

# 2. Gerar um roteiro para a fila de aprovação
python -m dark_channels.script --channel exemplo --topic "O mistério do voo 914"

# 3. Aprovar: mude "status: pending" para "status: approved" no arquivo em queue/exemplo/

# 4. Narrar o roteiro aprovado (VoiceStudio local, custo US$ 0)
python -m dark_channels.voice --channel exemplo --script queue/exemplo/<arquivo>.json

# 5. Montar o vídeo longo (precisa do manifesto de mídias, com licenças)
python -m dark_channels.render --narracao data/exemplo/<roteiro>/narracao/manifesto.json \
    --midias data/exemplo/<roteiro>/midias.json --saida data/exemplo/<roteiro>/video.mp4

# 6. Queimar a legenda no vídeo longo
python -m dark_channels.subtitles --narracao data/exemplo/<roteiro>/narracao/manifesto.json \
    --queimar data/exemplo/<roteiro>/video.mp4 --saida data/exemplo/<roteiro>/video-legendado.mp4

# 7. Gerar os Shorts verticais que o roteiro pediu
python -m dark_channels.shorts --roteiro queue/exemplo/<arquivo>.json \
    --narracao data/exemplo/<roteiro>/narracao/manifesto.json \
    --video data/exemplo/<roteiro>/video.mp4 --pasta data/exemplo/<roteiro>/shorts

# 8. Enviar um vídeo pronto, agendado
python -m dark_channels.upload --channel exemplo --video out/video.mp4 --script queue/exemplo/<arquivo>.json --publish-at 2026-11-20T18:00:00-03:00
```

## Narração local (sem chave, sem custo)

A narração sai do [VoiceStudio](https://github.com/debpalash/VoiceStudio), que
roda inteiro na máquina e expõe um endpoint compatível com a API da OpenAI em
`POST /v1/audio/speech`. Não usamos ElevenLabs. Abra o aplicativo VoiceStudio
uma vez para ele subir o backend local; depois o `voice.py` o encontra sozinho
em `http://127.0.0.1:3900`.

O `voice.py` gera **um arquivo por cena** e um manifesto `manifesto.json` com a
duração medida de cada uma. Esse manifesto é o que sincroniza visuais, legendas
e cortes verticais — nenhum passo seguinte precisa adivinhar tempo.

GPU com menos de 16 GB de VRAM (caso da RTX 3060): se algum motor falhar com
`CUDA out of memory` ou `Triton compilation failed` na primeira geração, ligue
**Configurações → Desempenho → Desativar torch.compile**, ou exporte
`TORCH_COMPILE_DISABLE=1` antes de abrir o aplicativo.

### Escreva o roteiro com acentuação correta

O texto vai direto para o motor de voz, então a ortografia é a pronúncia.
Testado em 20/11/2026: `explicacao oficial`, sem cedilha e sem til, sai falado
como "Explicacal Oficial" e ainda faz o motor repetir o trecho no fim do
áudio. Com `explicação oficial` a mesma frase sai correta. Vale para todo
acento, inclusive em nomes próprios.

### Licença do motor de voz

O motor **não** pode ser um de `MOTORES_NAO_COMERCIAIS` (`voice.py`). O padrão
do próprio VoiceStudio, `omnivoice` (`k2-fsa/OmniVoice`), é CC-BY-NC e não
serve para canal monetizado — a narração é recusada antes de sair qualquer
requisição, mesmo que o `config.yaml` do canal não traga o campo `engine`.
O padrão do nosso código é o `supertonic3` (MIT + OpenRAIL-M).

## Mídia e licenças

O `render.py` **recusa** qualquer mídia sem `source` e `license` declarados, e
só aceita as fontes da lista de `FONTES_ACEITAS` (mídia própria, gerada
localmente, Pexels, Pixabay, biblioteca de áudio do YouTube, ou outra licença
registrada). Formato do `midias.json`:

```json
[
  {"path": "data/.../fundo-1.png", "source": "pexels",
   "license": "Pexels License", "credit": "Fulano",
   "url": "https://www.pexels.com/photo/...", "scene": 0}
]
```

## Regras para não perder a monetização

- Roteiro original por vídeo, sempre aprovado por um humano antes de publicar.
- Nada de clipes de terceiros. Só mídia própria, gerada ou com licença registrada.
- Todo upload marca `containsSyntheticMedia` (selo de IA do YouTube).
- Volume moderado: nada de dezenas de vídeos quase iguais por dia.

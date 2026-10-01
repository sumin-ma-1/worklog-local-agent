# Worklog Local Agent

**Turn Telegram work chats into daily journals — on your machine.**

Worklog collects messages from the Telegram chats you already use, archives attachments, and writes a structured daily journal with a local model. You get finished notes for the day, not another chat transcript to skim.

It runs where your data already is: your Telegram account session, your disk, and an Ollama model you control. No third-party inbox scraping, no mandatory cloud LLM.

---

## What it does

- **Collect** — incrementally read selected work chats with your own Telegram user session (Telethon), not a bot sitting in every room.
- **Archive** — store photos, documents, and other files by date and chat.
- **Organize** — group a day’s messages in your timezone.
- **Journal** — generate a markdown worklog with a local Ollama model (or swap the model via config).
- **Dashboard** — browse journals, calendars, attachments, schedules, and share read-only links.
- **Telegram assistant** — optional Bot API helper for asking about journals and kicking off generation from chat.

```
Telegram chats → collect → archive → organize → journal (Ollama) → dashboard / share
```

## Why local

Your chats and attachments stay under `data/` on the host. Model calls go to the Ollama endpoint you configure. API credentials live in `.env`. Multi-user accounts isolate each person’s data under `data/users/<id>/`.

## Quick start

**Requirements:** Python 3.10+, a Telegram account with [API ID / Hash](https://my.telegram.org), and [Ollama](https://ollama.com) running locally.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
cp config.example.yaml config.yaml
```

Put `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in `.env`. Optionally set `TELEGRAM_BOT_TOKEN` and `bot_username` for the chat assistant.

```bash
ollama serve
ollama pull gemma4:e4b   # or set journal.ollama.model / OLLAMA_MODEL
```

### CLI

```bash
worklog-agent auth                 # phone + code login
worklog-agent chats                # list dialogs
worklog-agent run                  # collect → journal for today
worklog-agent run --date 2026-09-02

# or step by step
worklog-agent collect
worklog-agent archive
worklog-agent organize --date 2026-09-02
worklog-agent journal --date 2026-09-02
```

### Dashboard

```bash
worklog-agent dashboard
# http://127.0.0.1:8787
```

Sign in, link Telegram, pick the chats to watch, generate journals, schedule runs, and share a read-only link when you need to.

### Docker

```bash
docker compose up -d --build
```

See [`docker-compose.yml`](docker-compose.yml) for hosting notes.

## Data layout

```
data/
  users/<account_id>/
    sessions/              Telethon session
    raw/<chat_id>/         message JSONL
    attachments/<date>/…
    daily/<date>.json
    journals/<date>.md
    schedules.json
    shares.json
```

## Configuration

| Piece | Where |
| --- | --- |
| Telegram API + optional bot token | `.env` |
| Timezone, Ollama host/model, bot username | `config.yaml` |
| Public dashboard URL (shares, bot links) | `DASHBOARD_URL` in `.env` |

Defaults target Korean journals (`journal.language: ko`) and `gemma4:e4b`; change freely.

## How a day gets written

1. You (or a schedule) pick a date and generation mode — combined, per-room, or both.
2. Worklog pulls new messages for watched chats, saves attachments, and builds the daily bundle.
3. Ollama writes the journal sections you configured.
4. The dashboard shows the result; share links stay read-only for recipients.

## Privacy

- Runs on your host; no Worklog cloud backend.
- Telegram access uses **your** user session for collection and, optionally, a BotFather bot for Q&A.
- Journals and attachments are files you can copy, back up, or delete.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

See repository license / project metadata as applicable.

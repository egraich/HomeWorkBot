# HomeWorkBot

A Telegram bot that does tomorrow's homework with you: tell it exercise numbers, and it finds the pages in your textbook PDFs, reads them (OCR + vision), plans the work, solves tasks step by step and writes human-sounding essays — all on the free [Hack Club AI](https://ai.hackclub.com) API.

<!-- TODO: add a screenshot of a bot chat as docs/screenshot.png and uncomment:
![HomeWorkBot in action](docs/screenshot.png) -->

## Quick start (Docker)

```bash
git clone https://github.com/egraich/homeworkbot.git
cd homeworkbot
cp .env.example .env        # then fill it in (see Configuration below)
mkdir -p data
cd src && docker compose up -d --build
```

The bot only makes outbound connections (Telegram long polling + the AI API), so it runs on any machine with Docker — a server, a VPS, a home PC — without opening any ports.

## Features

- 🎧 **Listening mode** — dump homework as text, forwards or photos; images are OCR'd (handwriting and blackboards, Russian included) and acknowledged with a short receipt
- 🧠 **Understands what you mean, not just what you type** — an intent-extraction model reads each message together with the session context: "check against the answers" after exercise 1.42 resolves to 1.42 without repeating the number
- 📖 **Semantic textbook search** — every PDF page is embedded at ingest (`qwen3-embedding-8b`); queries are matched by meaning via cosine similarity plus exact-number FTS, so "explain the powers topic" finds the right pages even when the text layer of the PDF is mangled
- 👁 **Vision over pages** — when the brain model supports images, matched pages are rendered from the PDF and attached as pictures, so formulas are read the way they are printed, not from a broken text layer
- ✍️ **Human-sounding essays** — a two-stage pipeline: the brain drafts the content, a writer model rewrites it with an anti-AI-cliché prompt (mixed sentence lengths, banned stock phrases, no lists); math in answers renders as Telegram-native Unicode (a¹ᐟ³, √, ·)
- 🧮 **Per-task model routing** — receipts, OCR, reasoning and writing each get their own model per quality mode (econ / medium / max) with automatic fallback chains and a live model catalog check at startup
- 💰 **Budget guard** — every call is priced from live per-token prices and logged in USD against the daily limit: warns at 80%, blocks paid models at 95%, free-model receipts keep working

## Running locally

Requires **Python 3.11+** (nothing else — SQLite, FTS5 ship with CPython).

```bash
python -m venv .venv
.venv/bin/pip install -r src/requirements.txt   # Windows: .venv\Scripts\pip install -r src\requirements.txt
cp .env.example .env                            # then fill it in (see below)
cd src
../.venv/bin/python scripts/smoke_test.py       # live-check the API and all routed models
../.venv/bin/python -m bot
```

### Configuration (.env)

`.env` holds real secrets and is git-ignored — never commit it. `.env.example` is the template the repo ships with: copy it to `.env` and replace the placeholders.

| Variable | Example | What it is |
|---|---|---|
| `BOT_TOKEN` | `123456:ABC-...` | token from [@BotFather](https://t.me/BotFather) |
| `HACKCLUB_API_KEY` | `sk-hc-v1-...` | API key from [ai.hackclub.com](https://ai.hackclub.com) (Hack Clubbers only) |
| `ADMIN_IDS` | `123456789` | Telegram user id (via @userinfobot); comma-separate for several admins |
| `ALLOWED_IDS` | `123,456` | ids of allowed users — the bot rejects everyone else |
| `DEFAULT_MODE` | `econ` | `econ` / `medium` / `max` quality preset |
| `DAILY_BUDGET_USD` | `3.0` | spend limit per UTC day |
| `LOG_LEVEL` | `INFO` | log verbosity: `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TG_API_BASE` | `http://tg-bot-api:8081` | optional: a self-hosted [telegram-bot-api](https://github.com/AIPOW/telegram-bot-api) server, removes Telegram's 20 MB file limit |

## How it works

**Model routing.** Every LLM call goes through `bot/services/llm/client.py` and is labeled with a *task*: `quick` (receipts, intent extraction), `ocr`, `brain` (planning, solving, dialog), `writer` (essays), `embed` (semantic index). A plain dict in `router.py` maps task × quality mode to a model id plus a fallback chain; the live catalog (`GET /proxy/v1/models`) is fetched at startup, so unknown ids are dropped and per-model prices drive real cost accounting. Two gateway quirks are handled explicitly: "thinking" models burn small `max_tokens` budgets on invisible reasoning and return empty content (treated as a failure → next candidate), and some models reject `reasoning: {"enabled": false}` (extra params apply only to primaries).

**Textbook pipeline.** On ingest, PyMuPDF extracts text per page; empty pages are treated as scans, rendered to JPEG and OCR'd by a vision model under a semaphore. Pages go into an FTS5 index *and* an embeddings table (`pages_vec`, float32 blobs) — reindexing a book from the admin panel rebuilds both. At query time an intent model turns the message (plus recent session history) into exercise refs and keywords; refs go to exact FTS matches, the message + keywords are embedded and matched against page vectors by cosine similarity. For vision-capable brains the top matches are also rendered back from the PDF and attached as images, which is what makes formula-heavy textbooks actually readable.

**Essays.** Requests matching an essay pattern run a two-stage pipeline: the brain model produces a content skeleton, then the writer model rewrites it under a prompt that bans stock AI phrases (typical model filler openers like "in today's world" or "it is worth noting"), enforces mixed sentence lengths and forbids lists — the output reads like a student wrote it. Solved math is normalized to Telegram-native Unicode by a LaTeX-to-Unicode converter as a safety net.

**Budget.** Hack Club AI allows $3/day per account, shared by everyone using the bot. Each call's cost is computed from live per-token pricing and stored; paid calls are refused past 95% of the budget, with a warning at 80%. The counter resets at 00:00 UTC, same as the provider's.

## Deployment

The `docker-compose.yml` keeps runtime data (SQLite database, textbooks) outside the source tree in a `data/` folder next to `src/`, so it survives redeploys. Works the same on any Linux server, VPS or home machine with Docker:

```bash
cd src && docker compose up -d --build && docker compose logs -f homewbot
```

Redeploys are `git pull && docker compose up -d --build`; wire them to a webhook listener ([webhook](https://github.com/adnanh/webhook)) for push-to-deploy.

### Using a self-hosted Telegram Bot API server (optional)

A self-hosted [telegram-bot-api](https://github.com/AIPOW/telegram-bot-api) removes Telegram's 20 MB download limit, so textbooks can be sent as documents right in the chat. Put its container and the bot on a shared Docker network, point `TG_API_BASE` in `.env` at its address (e.g. `http://tg-bot-api:8081`), and log the bot token out of the cloud API once:

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/logOut"
```

Run the server in local mode (`TELEGRAM_LOCAL=1`) and mount its data directory into the bot's container **at the same path** — in local mode the server returns absolute file paths, so the bot reads files from disk instead of HTTP. Keep such server-specific edits in an untracked `src/docker-compose.override.yml` (compose merges it automatically) so `git pull` never conflicts:

```yaml
services:
  homewbot:
    volumes:
      - ../data:/app/data
      - /path/to/telegram-bot-api-data:/var/lib/telegram-bot-api:ro
```

## Textbooks

Two ways to add a book: send the PDF to the bot (`/admin` → Books → Upload via Telegram, then pick a subject), or drop the file into `data/textbooks/` and bind it in the same menu. Ingestion extracts text page-by-page, OCRs scans, embeds every page for semantic search and reports progress in chat; the admin panel can reindex a registered book when its model index needs rebuilding.

## Credits

- Runs on the free [Hack Club AI](https://ai.hackclub.com) gateway (OpenRouter-backed: DeepSeek, Qwen, Gemini, GPT, GLM and more)
- [aiogram 3](https://github.com/aiogram/aiogram) for the Telegram side, [PyMuPDF](https://pymupdf.readthedocs.io/) for PDF extraction

> The `HACKCLUB_API_KEY` only works for Hack Club members, and the service has its own [terms of use](https://docs.ai.hackclub.com/guide/rules.html) — read them before deploying.

---

Made by [egraich](https://egraich.dev) <3

# HomeWorkBot

A Telegram bot that collects tomorrow's homework from texts, forwarded messages and whiteboard photos, then plans it, solves it and writes essays on command — powered by the free [Hack Club AI](https://ai.hackclub.com) API.

<!-- TODO: drop a screenshot of a bot chat here as docs/screenshot.png and uncomment:
![HomeWorkBot in action](docs/screenshot.png) -->

## Quick start (Docker, on a server)

```bash
git clone https://github.com/egraich/homeworkbot.git homewbot && cd homewbot
cp .env.example .env && nano .env      # fill in your token, keys and ids
mkdir -p data && cd src && docker compose up -d --build
```

The bot runs wherever Docker runs: it only makes outbound connections (Telegram long polling + the AI API), so no open ports are needed.

## Features

- 🎧 **Listening mode** — dump homework as text, forwarded messages or photos; images are OCR'd (handwriting and blackboards, Russian included) and acknowledged with a short receipt
- 📋 **Smart planning** — one command turns everything you dumped into "what I understood" plus a structured per-task plan with difficulty ratings
- 📖 **Searches your textbooks** — PDFs are indexed per page (FTS5); scanned pages go through vision OCR; write "упражнение 214" and the bot pulls the right pages into context
- 🧠 **Model routing** — every task type (OCR, reasoning, essay writing, receipts) gets its own model per quality mode (econ / medium / max), with automatic fallbacks when a model fails
- ✍️ **Human-sounding essays** — a two-stage pipeline: the brain drafts the content, a writer model rewrites it with an anti-AI-cliché system prompt (sentence-length variance, banned stock phrases, no lists)
- 💰 **Budget guard** — tracks every call in USD against the shared daily limit, warns at 80% and blocks paid models at 95% (free-model receipts keep working)
- 🛠 **Admin panel in chat** — switch quality modes, check today's spend per role, upload/index textbooks, manage classes and subjects

## Running locally

Requires **Python 3.11+** (nothing else — SQLite and FTS5 ship with CPython).

```bash
python -m venv .venv
.venv\Scripts\pip install -r src\requirements.txt   # Linux/macOS: .venv/bin/pip ...
copy src\.env.example .env                          # fill it in (see below)
cd src
..\.venv\Scripts\python scripts\smoke_test.py       # live-check the API and all routed models
..\.venv\Scripts\python -m bot
```

### Configuration (.env)

| Variable | Example | What it is |
|---|---|---|
| `BOT_TOKEN` | `123456:ABC-...` | token from [@BotFather](https://t.me/BotFather) |
| `HACKCLUB_API_KEY` | `sk-hc-v1-...` | API key from [ai.hackclub.com](https://ai.hackclub.com) (Hack Clubbers only) |
| `ADMIN_IDS` | `123456789` | your Telegram id (via @userinfobot); comma-separate for several |
| `ALLOWED_IDS` | `123,456` | ids of allowed users — the bot rejects everyone else |
| `DEFAULT_MODE` | `econ` | `econ` / `medium` / `max` quality preset |
| `DAILY_BUDGET_USD` | `3.0` | spend limit per UTC day |
| `TG_API_BASE` | `http://tgapibot:8080` | optional: your own [telegram-bot-api](https://github.com/AIPOW/telegram-bot-api) server, removes Telegram's 20 MB file limit |

## How it works

**Model routing.** Every LLM call goes through `bot/services/llm/client.py` and is labeled with a *task*: `quick` (receipts), `ocr`, `brain` (planning, solving, dialog), `writer` (essays). A plain dict in `router.py` maps task × quality mode to a model id plus a fallback chain. The live catalog (`GET /proxy/v1/models`) is fetched at startup, so unknown ids are dropped and per-model prices drive real cost accounting. Two gateway quirks are handled explicitly: "thinking" models burn small `max_tokens` budgets on invisible reasoning and return empty content (treated as a failure → next candidate), and some models reject `reasoning: {"enabled": false}` (extra params apply only to primaries).

**Textbooks.** On ingest, PyMuPDF extracts text per page; pages under a character threshold are considered scans, rendered to JPEG and OCR'd by a vision model under a semaphore. Everything lands in `pages` + an FTS5 index, and homework messages mentioning exercise numbers (`№ 214`, `упр 5`, `стр 33`) pull the matching pages into the model's context — so answers cite the actual textbook instead of hallucinating.

**Essays.** Requests matching an essay pattern run a two-stage pipeline: the brain model produces a content skeleton, then the writer model rewrites it under a prompt that bans stock AI phrases («в современном мире», «стоит отметить»…), enforces mixed sentence lengths and forbids lists — the output reads like a student wrote it.

**Budget.** Hack Club AI gives $3/day per account, shared by everyone using the bot. Each call's cost is computed from live per-token pricing and stored; paid calls are refused past 95% of the budget, with a warning at 80%. The counter resets at 00:00 UTC, same as the provider's.

## Deploying to a VPS

The repo is laid out for a `~/projects/bots/<name>/src` server layout (data lives in `homewbot/data/` next to `src`, mounted into the container):

```bash
cd ~/projects/bots
git clone https://github.com/egraich/homeworkbot.git homewbot
cd homewbot && cp .env.example .env && nano .env
mkdir -p data && cd src && docker compose up -d --build && docker compose logs -f homewbot
```

Redeploys can go through a webhook listener ([almir/webhook](https://github.com/adnanh/webhook)) the same way the other bots on the box do: a `git pull` + `docker compose up -d --build` script triggered by an authenticated curl. To route the bot through your own Telegram Bot API server instead of the cloud one (removes the 20 MB limit): put the server's container on a shared Docker network, set `TG_API_BASE` to its address, and call `logOut` on the cloud API once for the token:

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/logOut"
```

## Textbooks

Two ways to add a book: send the PDF to the bot (`/admin` → Books → Upload via Telegram, auto-registered to a subject you pick), or drop the file into `data/textbooks/` over SSH and bind it in the same menu. Ingestion extracts text page-by-page, OCRs scans, and reports progress in chat.

## Credits

- Built by [egraich](https://github.com/egraich) as a Hack Club project, running on the free [Hack Club AI](https://ai.hackclub.com) gateway (OpenRouter-backed: DeepSeek, Qwen, Gemini, GPT, GLM and more)
- [aiogram 3](https://github.com/aiogram/aiogram) for the Telegram side, [PyMuPDF](https://pymupdf.readthedocs.io/) for PDF extraction

**Fair use:** Hack Club AI is for teens and forbids proxies and resale. This bot stays within the rules by being private (whitelist-only), keeping the API key server-side and being completely free for its users. Don't monetize it, don't publish your `.env`.

# HomeWorkBot 🎒 (`homewbot`)

Приватный Telegram-бот, который собирает домашку на завтра и помогает её сделать.
Работает на бесплатном API [ai.hackclub.com](https://ai.hackclub.com) (для Hack Clubbers),
рассчитан на 1–3 пользователей, хранит учебники в PDF и умеет искать по ним.

Расположение на проде: `~/projects/bots/homewbot/` — код в `src/`,
данные (SQLite + учебники) в `homewbot/data/`, как у соседних ботов.

## Как это работает

1. **/start** → выбираешь свой класс (если их несколько).
2. **«Домашка на завтра»** → отмечаешь предметы, по которым задали.
3. Бот уходит в **режим слушания**: кидай текст, пересылай сообщения, фотографируй
   доску и упражнения. Фото распознаются (OCR), бот коротко подтверждает, что принял.
   Если задание из учебника — просто напиши номер, бот сам найдёт страницы.
4. **/план** → модель сначала «думает вслух» (что поняла, где подвох),
   потом выдаёт структурированный план по всем задачам.
5. Дальше — **диалог**: «реши 2», «сочинение на тему…», «объясни шаг 3».
   Для каждой задачи подбирается своя модель (см. роутинг ниже).
6. **/стоп** — завершить сессию, **/сброс** — начать заново, **/стиль** —
   переключить стиль сочинений (чистый ↔ живой).

## Стек

- **Python 3.12+, aiogram 3** (long polling — порты открывать не нужно)
- **SQLite + FTS5** (aiosqlite) — юзеры, сессии, страницы учебников, полнотекстовый поиск
- **PyMuPDF** — извлечение текста из PDF; пустые страницы (сканы) → vision-OCR
- **OpenAI-совместимый клиент** → `https://ai.hackclub.com/proxy/v1`
- **Docker + docker-compose** — деплой как у остальных ботов на сервере

## Роутинг моделей

Один вызов = одна роль. Режим качества переключается в админке на лету.

| Роль | 🟢 Эконом | 🟡 Средний | 🔴 Макс |
|---|---|---|---|
| OCR фото | qwen3.8-flash | gemini-3.8-flash | gemini-3.8-flash |
| Мозг (план, решение, диалог) | deepseek-v4.1-flash | deepseek-v4-pro-0813 | gpt-6-astra |
| Писатель (сочинения) | deepseek-v4.1-flash | qwen3.8-max-0902 | gpt-terra-latest |
| Мелочь (квитанции) | ling-3.0-flash-vl:free | qwen3.8-flash | qwen3.8-flash |

Все 10 моделей из таблицы и фолбэков проверены живыми вызовами (2026-09-13).
`anthropic/claude-fable-5.1` исключён: шлюз hackai отдаёт на него 404 (guardrail).
`gpt-6-astra` иногда error-ит на стороне шлюза — если упал, фолбэк уводит на
glm-5.3 → terra → deepseek-v4-pro, бот не молчит.

Таблица — в `src/bot/services/llm/router.py`, правится без знания кода.
У каждой роли есть цепочка фолбэков: модель недоступна/промолчала → берётся следующая.
Нюанс: у `z-ai/glm-5.3` reasoning отключить нельзя (API отвечает 400), поэтому в роли
писателя эконома он не используется.

## Быстрый старт (локально)

```bash
python -m venv .venv
copy src\.env.example .env        # .env — на уровне папки бота, рядом с src (как на проде)
# ... заполни .env (см. таблицу ниже) ...
.venv\Scripts\pip install -r src\requirements.txt
cd src
..\.venv\Scripts\python scripts\smoke_test.py   # проверка API и моделей
..\.venv\Scripts\python -m bot                  # запуск бота
```

## Деплой на VPS

Клонировать как соседние боты:

```bash
cd ~/projects/bots
git clone <твой-репо> homewbot
cd homewbot
cp src/.env.example .env && nano .env   # .env на уровне homewbot/, как у voicebot
mkdir -p data
cd src && docker compose up -d --build
docker compose logs -f homewbot
```

Редеплой через webhook-стек `~/projects/ups` — точно как у остальных ботов.
Файлы деплоя (`deploy/up-homewbot.sh`, `deploy/hooks.json`) не в гите — там токен
вебхука; они лежат локально и на VPS:

```bash
cp ~/projects/bots/homewbot/deploy/up-homewbot.sh ~/projects/ups/scripts/
# и добавь блок up-homewbot из deploy/hooks.json в /root/projects/ups/hooks.json
# (webhook крутится с -hotreload, перезапуск не нужен)
```

Дальше деплой после пуша в гит = один curl:

```bash
curl "http://<vps>:9000/hooks/up-homewbot?token=<твой-webhook-токен>"
```

Скрипт делает ровно то же, что соседи: `git pull origin main` →
`docker compose up -d --build` → prune образов.

Проверка: `docker compose ps` — контейнер `homewbot`, статус healthy.

## Заполнение .env

| Переменная | Что это |
|---|---|
| `BOT_TOKEN` | токен бота. В Telegram: **@BotFather** → `/newbot` → имя → username (должен кончаться на `bot`) → токен вида `123456:ABC-...` |
| `HACKCLUB_API_KEY` | ключ с [ai.hackclub.com](https://ai.hackclub.com) (dashboard → Keys) |
| `ADMIN_IDS` | твой Telegram ID (узнать: написать **@userinfobot**). Через запятую, если админов несколько |
| `ALLOWED_IDS` | ID тех, кому разрешён бот (одноклассник и т.п.) |
| `DEFAULT_MODE` | `econ` / `medium` / `max` — режим по умолчанию |
| `DAILY_BUDGET_USD` | дневной лимит расходов (у hackai $3/день) |
| `TG_API_BASE` | адрес своего Bot API сервера (см. ниже) |

⚠️ У hackai **$3/день на весь аккаунт** — общий на всех юзеров бота. Сброс в 00:00 UTC
(03:00 МСК). Бот считает расход, предупреждает на 80% и блокирует платные вызовы на 95%
(квитанции на бесплатной модели продолжают работать). Статистика — `/admin` → «Расходы».

## Учебники

Два способа — на выбор:

**Через ТГ (проще всего):** `/admin` → **📖 Книги** → **📥 Загрузить книгу через ТГ**
→ пришли PDF документом → выбери предмет → инжест с прогрессом.
Лимит: 20 МБ через официальный API; через tgapibot — без лимита.

**По SSH:** закинь PDF в `~/projects/bots/homewbot/data/textbooks/`:
```bash
scp algebra_9.pdf root@vps:~/projects/bots/homewbot/data/textbooks/
```
и привяжи в `/admin` → **📖 Книги**.

Дальше одинаково: текстовые страницы извлекаются PyMuPDF, пустые (сканы)
рендерятся в картинки и распознаются vision-моделью; всё попадает в FTS-индекс —
бот находит «упражнение 214» за миллисекунды.

## Свой Telegram Bot API сервер (уже подключён)

На сервере крутится `~/projects/bots/tgapibot` (`aiogram/telegram-bot-api`, `TELEGRAM_LOCAL=true`),
он живёт в external-сети **`voicenet`** — контейнер homewbot уже подключён к ней в
compose. В `.env` стоит `TG_API_BASE=http://tgapibot:8080` — бот ходит к Telegram
через свой сервер, лимит Bot API в 20 МБ снят: PDF-учебники можно будет кидать
прямо в чат, бот скачает их через `http://tgapibot:8080/file/...`.

Если нужно вернуть официальный api.telegram.org — просто очисти `TG_API_BASE` в `.env`
и пересоздай контейнер. Код не меняется.

Важно при первом переводе бота на tgapibot: токен «залогинен» в облачном API, и
локальный сервер не примет его, пока ты его там не разлогинишь. Один раз:
```bash
curl "https://api.telegram.org/bot<ТОКЕН_БОТА>/logOut"
```
после этого запускай через tgapibot. Если что-то не взлетело — глянь
`docker logs tgapibot --tail 50` и `docker network inspect voicenet`
(оба контейнера должны быть в сети).

## Админка (`/admin`)

- **Режим качества** — econ/medium/max, применяется сразу для всех задач.
- **Расходы сегодня** — сумма, разбивка по ролям, остаток до $3.
- **Книги** — привязка PDF из папки к предметам, прогресс инжеста, список книг.
- **Классы и предметы** — добавление классов, добавление/удаление предметов.

## Структура проекта

```
homewbot/                  # = корень этого репо
├── deploy/                # НЕ в гите (там токен вебхука): up-homewbot.sh + hooks.json
│   ├── up-homewbot.sh     # → скопировать в ~/projects/ups/scripts/
│   └── hooks.json         # блок up-homewbot → добавить в ~/projects/ups/hooks.json
├── .env                   # секреты (не в гите), на уровне бота — как у соседних
├── LICENSE
├── .gitignore
└── src/                   # == контекст сборки Docker
    ├── Dockerfile  docker-compose.yml  .dockerignore
    ├── .env.example
    ├── requirements.txt  requirements-dev.txt
    ├── bot/
    │   ├── __main__.py    # точка входа (python -m bot)
    │   ├── config.py  states.py  keyboards.py  middlewares.py
    │   ├── handlers/      # common (меню), session (домашка), dialog, admin
    │   ├── services/
    │   │   ├── llm/       # client, router (модели), usage (бюджет), catalog, prompts
    │   │   ├── ocr.py  textbooks.py  planner.py  container.py
    │   ├── db/            # schema.sql + repo.py (весь SQL)
    │   └── utils/         # сплит 4096, md→HTML, стриминг ответа в чат
    ├── scripts/
    │   └── smoke_test.py  # живой тест API: ключ, модели, все роли, стриминг
    └── tests/             # юнит-тесты БД и инжеста (pytest)
```

Данные (не в гите): `homewbot/data/bot.sqlite3` + `homewbot/data/textbooks/` —
в контейнер монтируются как `/app/data`.

## Разработка

```bash
cd src
python scripts/smoke_test.py   # живой тест hackai (~$0.004 за прогон)
python -m pytest tests/ -q     # юнит-тесты без сети
```

## Правила ai.hackclub.com (важно)

Сервис — для подростков ≤18, запрещены прокси и перепродажа. Этот бот легален ровно
потому, что: он приватный (доступ только по whitelist из `.env`), пользователи не
получают доступ к ключу/API, бот бесплатный. Не выкладывай `.env` и не монетизируй бота.

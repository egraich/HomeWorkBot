-- Схема HomeWorkBot. datetime('now') в SQLite — всегда UTC,
-- что совпадает с механикой дневного бюджета hackai (сброс 00:00 UTC).

CREATE TABLE IF NOT EXISTS users (
    tg_id     INTEGER PRIMARY KEY,
    username  TEXT,
    full_name TEXT,
    role      TEXT NOT NULL DEFAULT 'user',          -- user | admin
    class_id  INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS classes (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS subjects (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    name     TEXT NOT NULL,
    UNIQUE (class_id, name)
);

CREATE TABLE IF NOT EXISTS textbooks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL UNIQUE,
    title      TEXT,
    pages      INTEGER NOT NULL DEFAULT 0,
    ocr_pages  INTEGER NOT NULL DEFAULT 0,           -- сколько страниц прошло через OCR
    is_scanned INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'ready',        -- ready | processing | failed
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    textbook_id INTEGER NOT NULL REFERENCES textbooks(id) ON DELETE CASCADE,
    page_no     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    UNIQUE (textbook_id, page_no)
);

-- Полнотекстовый индекс по страницам учебников (FTS5 есть в официальном
-- Python под Windows и в python:*-slim под Linux).
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(text, page_id UNINDEXED);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    tg_chat_id  INTEGER NOT NULL,
    class_id    INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    subject_ids TEXT NOT NULL DEFAULT '[]',            -- JSON-массив id предметов
    status      TEXT NOT NULL DEFAULT 'collecting',    -- collecting | dialog | done | cancelled
    essay_style TEXT NOT NULL DEFAULT 'clean',         -- clean | imperfect
    created_at  TEXT DEFAULT (datetime('now')),
    ended_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_active
    ON sessions (user_id, status);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,                          -- user | assistant
    kind       TEXT NOT NULL DEFAULT 'text',           -- text | forward | photo | plan
    content    TEXT NOT NULL,
    meta       TEXT,                                   -- JSON (file_id и пр.)
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT DEFAULT (datetime('now')),   -- UTC
    model             TEXT NOT NULL,
    task              TEXT NOT NULL,                    -- quick | ocr | brain | writer
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log (ts);

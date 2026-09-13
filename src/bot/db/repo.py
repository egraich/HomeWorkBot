"""SQLite data-access layer (aiosqlite). All SQL lives here."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass(slots=True)
class SessionInfo:
    id: int
    user_id: int
    tg_chat_id: int
    class_id: int | None
    subject_ids: list[int]
    status: str
    essay_style: str


def _session_from_row(row: aiosqlite.Row) -> SessionInfo:
    """Build a SessionInfo from a sessions table row."""
    return SessionInfo(
        id=row["id"],
        user_id=row["user_id"],
        tg_chat_id=row["tg_chat_id"],
        class_id=row["class_id"],
        subject_ids=json.loads(row["subject_ids"]),
        status=row["status"],
        essay_style=row["essay_style"],
    )


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the connection, apply pragmas and create tables."""
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        await self._conn.executescript(schema)
        await self._conn.commit()

    async def close(self) -> None:
        """Close the connection if open."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        """Return the active connection or raise if not connected."""
        if self._conn is None:
            raise RuntimeError("Database.connect() must be called first")
        return self._conn

    async def upsert_user(
        self, tg_id: int, username: str | None, full_name: str | None, is_admin: bool
    ) -> None:
        """Insert a user or refresh their profile fields."""
        role = "admin" if is_admin else "user"
        await self.conn.execute(
            """
            INSERT INTO users (tg_id, username, full_name, role)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
            """,
            (tg_id, username, full_name, role),
        )
        await self.conn.commit()

    async def get_user_class(self, tg_id: int) -> int | None:
        """Return the user's class id or None."""
        cur = await self.conn.execute(
            "SELECT class_id FROM users WHERE tg_id = ?", (tg_id,)
        )
        row = await cur.fetchone()
        return row["class_id"] if row else None

    async def set_user_class(self, tg_id: int, class_id: int | None) -> None:
        """Assign a class to the user."""
        await self.conn.execute(
            "UPDATE users SET class_id = ? WHERE tg_id = ?", (class_id, tg_id)
        )
        await self.conn.commit()

    async def list_classes(self) -> list[aiosqlite.Row]:
        """Return all classes ordered by name."""
        cur = await self.conn.execute("SELECT * FROM classes ORDER BY name")
        return list(await cur.fetchall())

    async def add_class(self, name: str) -> int:
        """Create a class (idempotent) and return its id."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO classes (name) VALUES (?)", (name,)
        )
        await self.conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        cur2 = await self.conn.execute("SELECT id FROM classes WHERE name = ?", (name,))
        row = await cur2.fetchone()
        return int(row["id"])

    async def get_class(self, class_id: int) -> aiosqlite.Row | None:
        """Return a class row by id or None."""
        cur = await self.conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,))
        return await cur.fetchone()

    async def list_subjects(self, class_id: int) -> list[aiosqlite.Row]:
        """Return subjects of one class ordered by name."""
        cur = await self.conn.execute(
            "SELECT * FROM subjects WHERE class_id = ? ORDER BY name", (class_id,)
        )
        return list(await cur.fetchall())

    async def list_all_subjects(self) -> list[aiosqlite.Row]:
        """Return all subjects joined with their class names."""
        cur = await self.conn.execute(
            """
            SELECT s.*, c.name AS class_name FROM subjects s
            JOIN classes c ON c.id = s.class_id
            ORDER BY c.name, s.name
            """
        )
        return list(await cur.fetchall())

    async def add_subject(self, class_id: int, name: str) -> int:
        """Create a subject (idempotent) and return its id."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO subjects (class_id, name) VALUES (?, ?)",
            (class_id, name),
        )
        await self.conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        cur2 = await self.conn.execute(
            "SELECT id FROM subjects WHERE class_id = ? AND name = ?", (class_id, name)
        )
        row = await cur2.fetchone()
        return int(row["id"])

    async def delete_subject(self, subject_id: int) -> None:
        """Delete a subject (cascades to its textbooks)."""
        await self.conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
        await self.conn.commit()

    async def get_subject(self, subject_id: int) -> aiosqlite.Row | None:
        """Return a subject row by id or None."""
        cur = await self.conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
        return await cur.fetchone()

    async def add_textbook(
        self, subject_id: int, filename: str, title: str | None
    ) -> int:
        """Register a textbook (idempotent) and return its id."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO textbooks (subject_id, filename, title) VALUES (?, ?, ?)",
            (subject_id, filename, title),
        )
        await self.conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        cur2 = await self.conn.execute(
            "SELECT id FROM textbooks WHERE filename = ?", (filename,)
        )
        row = await cur2.fetchone()
        return int(row["id"])

    async def textbook_ids_for_subjects(self, subject_ids: list[int]) -> list[int]:
        """Return ids of textbooks bound to the given subject ids."""
        if not subject_ids:
            return []
        placeholders = ",".join("?" * len(subject_ids))
        cur = await self.conn.execute(
            f"SELECT DISTINCT id FROM textbooks WHERE subject_id IN ({placeholders})",
            list(subject_ids),
        )
        return [int(r["id"]) for r in await cur.fetchall()]

    async def list_textbooks(self) -> list[aiosqlite.Row]:
        """Return all textbooks joined with subject and class names."""
        cur = await self.conn.execute(
            """
            SELECT t.*, s.name AS subject_name, c.name AS class_name
            FROM textbooks t
            JOIN subjects s ON s.id = t.subject_id
            JOIN classes c ON c.id = s.class_id
            ORDER BY t.created_at DESC
            """
        )
        return list(await cur.fetchall())

    async def get_textbook(self, textbook_id: int) -> aiosqlite.Row | None:
        """Return a textbook row by id or None."""
        cur = await self.conn.execute(
            "SELECT * FROM textbooks WHERE id = ?", (textbook_id,)
        )
        return await cur.fetchone()

    async def finish_textbook(
        self, textbook_id: int, pages: int, ocr_pages: int, is_scanned: bool
    ) -> None:
        """Mark a textbook as ready with ingest statistics."""
        await self.conn.execute(
            """
            UPDATE textbooks
            SET pages = ?, ocr_pages = ?, is_scanned = ?, status = 'ready'
            WHERE id = ?
            """,
            (pages, ocr_pages, int(is_scanned), textbook_id),
        )
        await self.conn.commit()

    async def fail_textbook(self, textbook_id: int) -> None:
        """Mark a textbook as failed after an ingest error."""
        await self.conn.execute(
            "UPDATE textbooks SET status = 'failed' WHERE id = ?", (textbook_id,)
        )
        await self.conn.commit()

    async def replace_pages(self, textbook_id: int, rows: list[tuple[int, str]]) -> None:
        """Replace all stored pages and rebuild the FTS index for one textbook."""
        conn = self.conn
        await conn.execute("DELETE FROM pages_fts WHERE page_id IN "
                           "(SELECT id FROM pages WHERE textbook_id = ?)", (textbook_id,))
        await conn.execute("DELETE FROM pages WHERE textbook_id = ?", (textbook_id,))
        for page_no, text in rows:
            cur = await conn.execute(
                "INSERT INTO pages (textbook_id, page_no, text) VALUES (?, ?, ?)",
                (textbook_id, page_no, text),
            )
            await conn.execute(
                "INSERT INTO pages_fts (text, page_id) VALUES (?, ?)",
                (text, cur.lastrowid),
            )
        await conn.commit()

    async def search_pages(
        self, textbook_ids: list[int], query: str, limit: int = 3
    ) -> list[dict]:
        """Full-text search over textbook pages, best matches first."""
        if not textbook_ids or not query.strip():
            return []
        # single digits matter ("exercise No. 5"), so keep them too
        tokens = [
            t for t in query.replace("№", " ").split()
            if len(t) >= 2 or t.isdigit()
        ][:8]
        if not tokens:
            return []
        match = " OR ".join('"' + t.replace('"', "") + '"' for t in tokens)
        placeholders = ",".join("?" * len(textbook_ids))
        cur = await self.conn.execute(
            f"""
            SELECT p.page_no, p.text, p.textbook_id
            FROM pages_fts f
            JOIN pages p ON p.id = f.page_id
            WHERE pages_fts MATCH ? AND p.textbook_id IN ({placeholders})
            ORDER BY rank
            LIMIT ?
            """,
            (match, *textbook_ids, limit),
        )
        rows = await cur.fetchall()
        return [
            {"page_no": r["page_no"], "text": r["text"], "textbook_id": r["textbook_id"]}
            for r in rows
        ]

    async def create_session(
        self, user_id: int, tg_chat_id: int, class_id: int | None, subject_ids: list[int]
    ) -> int:
        """Create a homework session in 'collecting' status and return its id."""
        cur = await self.conn.execute(
            """
            INSERT INTO sessions (user_id, tg_chat_id, class_id, subject_ids)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, tg_chat_id, class_id, json.dumps(subject_ids)),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def get_active_session(self, user_id: int) -> SessionInfo | None:
        """Return the user's latest active session or None."""
        cur = await self.conn.execute(
            """
            SELECT * FROM sessions
            WHERE user_id = ? AND status IN ('collecting', 'dialog')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        return _session_from_row(row) if row else None

    async def get_session(self, session_id: int) -> SessionInfo | None:
        """Return a session by id or None."""
        cur = await self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cur.fetchone()
        return _session_from_row(row) if row else None

    async def set_session_status(self, session_id: int, status: str) -> None:
        """Update session status, stamping ended_at on finish."""
        ended = ", ended_at = datetime('now')" if status in ("done", "cancelled") else ""
        await self.conn.execute(
            f"UPDATE sessions SET status = ?{ended} WHERE id = ?", (status, session_id)
        )
        await self.conn.commit()

    async def set_session_essay_style(self, session_id: int, style: str) -> None:
        """Switch the essay writing style of a session."""
        await self.conn.execute(
            "UPDATE sessions SET essay_style = ? WHERE id = ?", (style, session_id)
        )
        await self.conn.commit()

    async def clear_session_messages(self, session_id: int) -> None:
        """Delete all messages of a session."""
        await self.conn.execute(
            "DELETE FROM messages WHERE session_id = ?", (session_id,)
        )
        await self.conn.commit()

    async def add_message(
        self, session_id: int, role: str, kind: str, content: str, meta: dict | None = None
    ) -> int:
        """Append a message to a session and return its id."""
        cur = await self.conn.execute(
            "INSERT INTO messages (session_id, role, kind, content, meta) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, kind, content, json.dumps(meta) if meta else None),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def list_messages(self, session_id: int, limit: int | None = None) -> list[dict]:
        """Return session messages in chronological order (last `limit` if set)."""
        sql = "SELECT * FROM messages WHERE session_id = ? ORDER BY id"
        if limit:
            sql += f" DESC LIMIT {int(limit)}"
        cur = await self.conn.execute(sql, (session_id,))
        rows = list(await cur.fetchall())
        if limit:
            rows.reverse()
        return [
            {
                "role": r["role"],
                "kind": r["kind"],
                "content": r["content"],
                "meta": json.loads(r["meta"]) if r["meta"] else {},
            }
            for r in rows
        ]

    async def count_messages(self, session_id: int, role: str = "user") -> int:
        """Count messages of one role in a session."""
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND role = ?",
            (session_id, role),
        )
        row = await cur.fetchone()
        return int(row["n"])

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Return a key-value setting or the default."""
        cur = await self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        """Upsert a key-value setting."""
        await self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def add_usage(
        self, model: str, task: str, prompt_tokens: int, completion_tokens: int, cost_usd: float
    ) -> None:
        """Log one LLM call for spend tracking."""
        await self.conn.execute(
            """
            INSERT INTO usage_log (model, task, prompt_tokens, completion_tokens, cost_usd)
            VALUES (?, ?, ?, ?, ?)
            """,
            (model, task, prompt_tokens, completion_tokens, cost_usd),
        )
        await self.conn.commit()

    async def spent_today(self) -> float:
        """Return total spend (USD) since UTC midnight."""
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM usage_log "
            "WHERE ts >= datetime('now', 'start of day')"
        )
        row = await cur.fetchone()
        return float(row["total"])

    async def usage_today_breakdown(self) -> list[aiosqlite.Row]:
        """Return today's spend aggregated per task type."""
        cur = await self.conn.execute(
            """
            SELECT task, COUNT(*) AS calls, SUM(prompt_tokens) AS pt,
                   SUM(completion_tokens) AS ct, SUM(cost_usd) AS cost
            FROM usage_log
            WHERE ts >= datetime('now', 'start of day')
            GROUP BY task ORDER BY cost DESC
            """
        )
        return list(await cur.fetchall())

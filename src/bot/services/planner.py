"""Homework assembly: session materials + textbook excerpts -> model messages."""
from __future__ import annotations

from bot.db.repo import Database, SessionInfo
from bot.services.textbooks import extract_references

MAX_EXCERPT_CHARS = 1200
MAX_DIALOG_CHARS = 16000


async def collect_session_items(db: Database, session: SessionInfo) -> list[dict]:
    """Return all user-submitted materials of a session in chronological order."""
    msgs = await db.list_messages(session.id)
    return [m for m in msgs if m["role"] == "user"]


async def textbook_excerpts(
    db: Database, session: SessionInfo, texts: list[str], limit: int = 4
) -> list[dict]:
    """Find textbook pages matching exercise numbers and keywords from texts."""
    ids = session.subject_ids
    if not ids or not texts:
        return []

    queries: list[str] = []
    refs = [r for t in texts for r in extract_references(t)]
    for ref in refs:
        queries.append(ref)
    tail_words = [
        w for w in texts[-1].split() if len(w) >= 5 and not w.isdigit()
    ][:6]
    if tail_words:
        queries.append(" ".join(tail_words))

    seen: set[tuple[int, int]] = set()
    out: list[dict] = []
    for q in queries:
        if len(out) >= limit:
            break
        for page in await db.search_pages(ids, q, limit=limit):
            key = (page["textbook_id"], page["page_no"])
            if key in seen:
                continue
            seen.add(key)
            page["text"] = page["text"][:MAX_EXCERPT_CHARS]
            out.append(page)
            if len(out) >= limit:
                break
    return out


def trim_history(history: list[dict], max_chars: int = MAX_DIALOG_CHARS) -> list[dict]:
    """Trim dialog history to max_chars from the end, keeping user-first pairs."""
    total = 0
    cut = len(history)
    for i in range(len(history) - 1, -1, -1):
        total += len(history[i]["content"])
        if total > max_chars and i > 0:
            cut = i + 1
            break
    trimmed = history[cut:]
    while trimmed and trimmed[0]["role"] != "user":
        trimmed.pop(0)
    return trimmed

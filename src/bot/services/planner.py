"""Homework assembly: session materials + textbook excerpts -> model messages."""
from __future__ import annotations

import json
import logging
import re

from bot.db.repo import Database, SessionInfo
from bot.services.textbooks import extract_references

log = logging.getLogger(__name__)

MAX_EXCERPT_CHARS = 1200
MAX_DIALOG_CHARS = 16000


async def extract_intent(services, session: SessionInfo, texts: list[str]) -> dict:
    """Extract exercise refs and search keywords from texts via a cheap LLM.

    Falls back to regex extraction when the model fails or the budget blocks it.
    """
    recent = [
        m["content"][:300]
        for m in reversed(await services.db.list_messages(session.id, limit=6))
    ]
    try:
        from bot.services.llm.prompts import build_intent_messages

        res = await services.llm.chat(
            "quick",
            build_intent_messages(texts[-1][:1000], recent),
            temperature=0,
            max_tokens=150,
        )
        data = json.loads(re.search(r"\{.*\}", res.text, re.S).group(0))
        refs = [str(r) for r in (data.get("refs") or [])][:3]
        keywords = [str(k) for k in (data.get("keywords") or [])][:3]
        log.info("intent: refs=%s keywords=%s", refs, keywords)
        return {"refs": refs, "keywords": keywords}
    except Exception as e:  # noqa: BLE001
        log.info("intent extraction fell back to regex: %s", e)
        return {"refs": [r for t in texts for r in extract_references(t)], "keywords": []}


async def _recent_refs(db: Database, session: SessionInfo, limit: int = 12) -> list[str]:
    """Collect exercise numbers mentioned in recent session messages."""
    msgs = await db.list_messages(session.id, limit=limit)
    out: list[str] = []
    for m in reversed(msgs):  # newest first
        for r in extract_references(m["content"]):
            if r not in out:
                out.append(r)
        if len(out) >= 3:
            break
    return out


async def collect_session_items(db: Database, session: SessionInfo) -> list[dict]:
    """Return all user-submitted materials of a session in chronological order."""
    msgs = await db.list_messages(session.id)
    return [m for m in msgs if m["role"] == "user"]


async def textbook_excerpts(services, session: SessionInfo, texts: list[str], limit: int = 4) -> list[dict]:
    """Assemble textbook excerpts for texts: LLM intent -> FTS refs + vector search."""
    db = services.db
    textbook_ids = await db.textbook_ids_for_subjects(session.subject_ids)
    if not textbook_ids or not texts:
        return []

    intent = await extract_intent(services, session, texts)
    refs = intent["refs"] or await _recent_refs(db, session)

    out: list[dict] = []
    seen: set[tuple[int, int]] = set()

    def _add(pages: list[dict]) -> None:
        for page in pages:
            key = (page["textbook_id"], page["page_no"])
            if key in seen:
                continue
            seen.add(key)
            page["text"] = page["text"][:MAX_EXCERPT_CHARS]
            out.append(page)

    for ref in refs[:3]:
        variants = [ref]
        base = re.fullmatch(r"(\d+\.\d+)[а-я]", ref)
        if base:
            variants.insert(0, base.group(1))
        for q in variants:
            if len(out) >= limit:
                break
            _add(await db.search_pages(textbook_ids, q, limit=limit))

    if len(out) < limit:
        query_text = " ".join([texts[-1][:800], *intent["keywords"]])
        try:
            qvec = (await services.embedder.embed([query_text[:1000]]))[0]
            _add(await db.vector_search(textbook_ids, qvec, k=limit))
        except Exception as e:  # noqa: BLE001
            log.warning("vector search failed: %s", e)
    return out[:limit]


def trim_history(history: list[dict], max_chars: int = MAX_DIALOG_CHARS) -> list[dict]:
    """Trim dialog history to max_chars from the end, keeping user-first pairs."""
    total = 0
    cut = 0
    for i in range(len(history) - 1, -1, -1):
        total += len(history[i]["content"])
        if total > max_chars:
            cut = i + 1
            break
    trimmed = history[cut:]
    while trimmed and trimmed[0]["role"] != "user":
        trimmed.pop(0)
    return trimmed

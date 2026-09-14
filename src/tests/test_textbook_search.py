"""Textbook search tests: LLM intent refs, FTS, vector search, trimming."""
from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

from bot.db.repo import Database
from bot.services.planner import textbook_excerpts, trim_history
from bot.services.textbooks import extract_references


class _FakeLLM:
    """Returns a canned intent response."""

    def __init__(self, text: str) -> None:
        self.text = text

    async def chat(self, task, messages, **kwargs):
        return types.SimpleNamespace(text=self.text)


class _FakeEmbedder:
    """Returns one fixed vector for every text."""

    def __init__(self, vec: list[float]) -> None:
        self.vec = vec

    async def embed(self, texts):
        return [list(self.vec) for _ in texts]


def _services(db: Database, intent_text: str):
    return types.SimpleNamespace(
        db=db,
        llm=_FakeLLM(intent_text),
        embedder=_FakeEmbedder([1.0] * 8),
    )


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    """Provide a connected temporary database."""
    database = Database(tmp_path / "db.sqlite3")
    asyncio.run(database.connect())
    yield database
    asyncio.run(database.close())


def test_extract_references_dotted_and_plain():
    """Extract exercise numbers with dots, letters and plain single digits."""
    assert extract_references("реши упражнение 1.43 и № 5") == ["1.43", "5"]
    assert extract_references("стр. 33 и упр 214а") == ["33", "214а"]
    assert extract_references("№ 2,5") == ["2.5"]


def test_excerpts_llm_refs_fts(tmp_path: Path, db: Database):
    """LLM intent refs drive the FTS search across the subject's textbooks."""
    async def run():
        class_id = await db.add_class("11А")
        for name in ["Астрономия", "Белорусская Литература", "Белорусский Язык",
                     "Биология", "География", "Геометрия", "Алгебра"]:
            await db.add_subject(class_id, name)
        subjects = await db.list_subjects(class_id)
        algebra = next(s for s in subjects if s["name"] == "Алгебра")
        tb_id = await db.add_textbook(algebra["id"], "algebra11.pdf", "Алгебра 11")
        assert tb_id != algebra["id"]
        await db.upsert_user(1, "u", "U", False)
        await db.replace_pages(tb_id, [
            (18, "1.42. Упростите выражение 27^(1/3) и подобные"),
            (200, "ОТВЕТЫ. 1.42. а) 18,75; б) 3,2; в) 55; г) 1."),
        ])
        sid = await db.create_session(1, 1, class_id, [algebra["id"]])
        session = await db.get_session(sid)

        services = _services(db, '{"refs": ["1.42"], "keywords": ["ответы"]}')
        excerpts = await textbook_excerpts(
            services, session, ["проверь по ответам в книге"]
        )
        assert excerpts
        assert all(e["page_no"] in (18, 200) for e in excerpts)

    asyncio.run(run())


def test_excerpts_intent_fallback_to_regex(tmp_path: Path, db: Database):
    """Broken intent output falls back to regex refs extracted from the text."""
    async def run():
        class_id = await db.add_class("11А")
        subj = await db.add_subject(class_id, "Алгебра")
        tb_id = await db.add_textbook(subj, "algebra11.pdf", "Алгебра 11")
        await db.upsert_user(1, "u", "U", False)
        await db.replace_pages(tb_id, [
            (143, "1.43. Упростите выражение со степенями"),
        ])
        sid = await db.create_session(1, 1, class_id, [subj])
        session = await db.get_session(sid)

        services = _services(db, "не понял ничего")
        excerpts = await textbook_excerpts(services, session, ["упражнение 1.43"])
        assert excerpts and excerpts[0]["page_no"] == 143

    asyncio.run(run())


def test_vector_storage_and_search(tmp_path: Path, db: Database):
    """Page vectors roundtrip and cosine search return the closest page."""
    async def run():
        class_id = await db.add_class("11А")
        subj = await db.add_subject(class_id, "Алгебра")
        tb_id = await db.add_textbook(subj, "b.pdf", "b")
        await db.replace_pages(tb_id, [(1, "степени"), (2, "логарифмы")])
        await db.add_page_vectors(tb_id, [(1, [1.0, 0.0, 0.0]), (2, [0.0, 1.0, 0.0])])
        hits = await db.vector_search([tb_id], [0.9, 0.1, 0.0], k=2)
        assert hits[0]["page_no"] == 1
        hits = await db.vector_search([tb_id], [0.0, 1.0, 0.0], k=1)
        assert hits[0]["page_no"] == 2
        await db.delete_page_vectors(tb_id)
        assert await db.vector_search([tb_id], [1.0, 0.0, 0.0]) == []

    asyncio.run(run())


def test_trim_history_keeps_short_conversations():
    """A history that fits the limit must not be trimmed to nothing."""
    history = [
        {"role": "user", "kind": "text", "content": "упражнение 1.43"},
        {"role": "assistant", "kind": "plan", "content": "план с условием: упростите выражение"},
        {"role": "user", "kind": "text", "content": "реши 1"},
    ]
    trimmed = trim_history(history)
    assert trimmed == history

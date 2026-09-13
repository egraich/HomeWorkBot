"""Textbook search regression: subject->textbook mapping and dotted numbers."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.db.repo import Database, SessionInfo
from bot.services.planner import textbook_excerpts
from bot.services.textbooks import extract_references


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


def test_excerpts_use_textbook_ids_not_subject_ids(tmp_path: Path, db: Database):
    """Regression: subject id and textbook id differ — search must still work."""
    async def run():
        class_id = await db.add_class("11А")
        for name in ["Астрономия", "Белорусская Литература", "Белорусский Язык",
                     "Биология", "География", "Геометрия", "Алгебра"]:
            await db.add_subject(class_id, name)
        subjects = await db.list_subjects(class_id)
        algebra = next(s for s in subjects if s["name"] == "Алгебра")
        tb_id = await db.add_textbook(algebra["id"], "algebra11.pdf", "Алгебра 11")
        assert tb_id != algebra["id"]

        await db.replace_pages(tb_id, [
            (143, "Упражнение 1.43. Решите уравнение и постройте график функции"),
        ])

        session = SessionInfo(
            id=1, user_id=1, tg_chat_id=1, class_id=class_id,
            subject_ids=[algebra["id"]], status="collecting", essay_style="clean",
        )
        excerpts = await textbook_excerpts(db, session, ["задано упражнение 1.43"])
        assert excerpts and excerpts[0]["page_no"] == 143

    asyncio.run(run())

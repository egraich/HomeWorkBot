"""Тесты слоя БД: схема, CRUD, FTS-поиск, настройки, расходы."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from bot.db.repo import Database


def with_db(tmp_path: Path, body: Callable[[Database], Awaitable[None]]) -> None:
    """Открывает БД, гоняет body, закрывает — всё в одном asyncio.run()."""

    async def wrapper() -> None:
        db = Database(tmp_path / "db.sqlite3")
        await db.connect()
        try:
            await body(db)
        finally:
            await db.close()

    asyncio.run(wrapper())


def test_users_and_classes(tmp_path: Path):
    async def body(db: Database):
        await db.upsert_user(1, "egor", "Егор", True)
        await db.upsert_user(2, "misha", "Миша", False)
        assert await db.get_user_class(1) is None

        class_id = await db.add_class("9А")
        await db.add_class("9А")  # повтор не должен плодить классы
        classes = await db.list_classes()
        assert len(classes) == 1 and classes[0]["name"] == "9А"

        await db.set_user_class(1, class_id)
        assert await db.get_user_class(1) == class_id

        subj1 = await db.add_subject(class_id, "Алгебра")
        subj2 = await db.add_subject(class_id, "Физика")
        await db.add_subject(class_id, "Алгебра")
        assert len(await db.list_subjects(class_id)) == 2
        assert subj1 != subj2

        await db.delete_subject(subj2)
        assert len(await db.list_subjects(class_id)) == 1

    with_db(tmp_path, body)


def test_textbooks_pages_fts(tmp_path: Path):
    async def body(db: Database):
        class_id = await db.add_class("9А")
        subj = await db.add_subject(class_id, "Алгебра")
        tb_id = await db.add_textbook(subj, "algebra_9.pdf", "Алгебра 9")

        pages = [
            (10, "Квадрат суммы: (a+b)² = a² + 2ab + b². Упражнение 101."),
            (11, "Теорема Виета: сумма корней равна -b/a."),
            (12, "Упражнение 214: решите уравнение x² = 49."),
        ]
        await db.replace_pages(tb_id, pages)
        await db.finish_textbook(tb_id, pages=3, ocr_pages=0, is_scanned=False)

        tb = await db.get_textbook(tb_id)
        assert tb["pages"] == 3 and not tb["is_scanned"]

        hits = await db.search_pages([tb_id], "Виета корни", limit=3)
        assert hits and hits[0]["page_no"] == 11

        hits = await db.search_pages([tb_id], "214", limit=3)
        assert hits and hits[0]["page_no"] == 12

        # замена страниц удаляет старые записи и из FTS
        await db.replace_pages(tb_id, [(10, "Совсем другой текст про параболу")])
        hits = await db.search_pages([tb_id], "Виета", limit=3)
        assert hits == []

    with_db(tmp_path, body)


def test_sessions_and_messages(tmp_path: Path):
    async def body(db: Database):
        await db.upsert_user(1, "egor", "Егор", False)
        class_id = await db.add_class("9А")
        s1 = await db.add_subject(class_id, "Алгебра")
        s2 = await db.add_subject(class_id, "Физика")

        session_id = await db.create_session(1, 555, class_id, [s1, s2])
        session = await db.get_active_session(1)
        assert session is not None
        assert session.status == "collecting"
        assert session.subject_ids == [s1, s2]
        assert session.essay_style == "clean"

        await db.add_message(session_id, "user", "text", "реши номер 5")
        await db.add_message(session_id, "user", "photo", "OCR текст", {"file_id": "f1"})
        await db.add_message(session_id, "assistant", "plan", "План: ...")
        assert await db.count_messages(session_id) == 2

        msgs = await db.list_messages(session_id)
        assert msgs[0]["content"] == "реши номер 5"
        assert msgs[1]["meta"]["file_id"] == "f1"

        await db.set_session_status(session_id, "dialog")
        session = await db.get_active_session(1)
        assert session.status == "dialog"

        await db.set_session_essay_style(session_id, "imperfect")
        assert (await db.get_session(session_id)).essay_style == "imperfect"

        await db.set_session_status(session_id, "done")
        assert await db.get_active_session(1) is None

    with_db(tmp_path, body)


def test_settings_and_usage(tmp_path: Path):
    async def body(db: Database):
        assert await db.get_setting("quality_mode", "econ") == "econ"
        await db.set_setting("quality_mode", "max")
        await db.set_setting("quality_mode", "medium")
        assert await db.get_setting("quality_mode") == "medium"

        await db.add_usage("m1", "brain", 100, 50, 0.01)
        await db.add_usage("m2", "ocr", 200, 300, 0.005)
        spent = await db.spent_today()
        assert abs(spent - 0.015) < 1e-9

        rows = await db.usage_today_breakdown()
        assert {r["task"] for r in rows} == {"brain", "ocr"}

    with_db(tmp_path, body)

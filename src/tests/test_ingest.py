"""Тесты инжеста учебников: текстовые PDF и сканы (OCR-ветка, с моком)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pymupdf as fitz
import pytest

import bot.services.textbooks as tb_module
from bot.db.repo import Database
from bot.services.textbooks import TextbookService, unregistered_files


def make_text_pdf(path: Path, page_texts: list[str]) -> None:
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=400, height=300)
        page.insert_text((40, 60), text, fontsize=12)
    doc.save(path)
    doc.close()


def make_blank_pdf(path: Path, n: int = 3) -> None:
    doc = fitz.open()
    for _ in range(n):
        doc.new_page(width=400, height=300)  # пустые страницы = «скан»
    doc.save(path)
    doc.close()


class DummyLLM:  # OCR в текстовой ветке не вызывается
    pass


def _service(tmp_path: Path, db: Database) -> TextbookService:
    cfg = type("Cfg", (), {"data_dir": tmp_path, "textbooks_dir": tmp_path / "textbooks"})()
    cfg.textbooks_dir.mkdir(exist_ok=True)
    return TextbookService(cfg, db, DummyLLM())  # type: ignore[arg-type]


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "db.sqlite3")
    asyncio.run(database.connect())
    yield database
    asyncio.run(database.close())


def test_ingest_text_pdf(tmp_path: Path, db: Database, monkeypatch):
    async def run():
        service = _service(tmp_path, db)
        pdf = tmp_path / "textbooks" / "algebra.pdf"
        # ASCII: базовый шрифт PyMuPDF не умеет кириллицу (даёт точки).
        # Кириллический FTS-поиск покрыт в test_db.py напрямую через БД.
        make_text_pdf(pdf, [
            "Square of a sum: (a+b)^2 = a^2 + 2ab + b^2, examples and exercises",
            "Pythagorean theorem: the hypotenuse squared equals legs squared, c^2 = a^2 + b^2",
            "Exercise 214 on page 33: solve x^2 = 49 and check the roots",
        ])
        class_id = await db.add_class("9А")
        subj = await db.add_subject(class_id, "Алгебра")
        tb_id = await db.add_textbook(subj, pdf.name, "algebra")

        def _fail(*a, **kw):
            raise AssertionError("OCR не должен вызываться для текстового PDF")

        monkeypatch.setattr(tb_module, "ocr_image", _fail)

        stats = await service.ingest(pdf, subj, tb_id)
        assert stats == {"pages": 3, "ocr_pages": 0, "is_scanned": False}

        hits = await db.search_pages([tb_id], "Pythagorean", limit=3)
        assert hits and hits[0]["page_no"] == 2
        hits = await db.search_pages([tb_id], "214", limit=3)
        assert hits and hits[0]["page_no"] == 3

    asyncio.run(run())


def test_ingest_scanned_pdf_uses_ocr(tmp_path: Path, db: Database, monkeypatch):
    async def run():
        service = _service(tmp_path, db)
        pdf = tmp_path / "textbooks" / "scan.pdf"
        make_blank_pdf(pdf, n=4)

        class_id = await db.add_class("9А")
        subj = await db.add_subject(class_id, "Физика")
        tb_id = await db.add_textbook(subj, pdf.name, "scan")

        progress: list[tuple[int, int, int]] = []

        async def fake_ocr(client, image_bytes, mime="image/jpeg"):
            return "РАСПОЗНАННЫЙ ТЕКСТ СТРАНИЦЫ"

        async def progress_cb(done, total, pages):
            progress.append((done, total, pages))

        monkeypatch.setattr(tb_module, "ocr_image", fake_ocr)
        stats = await service.ingest(pdf, subj, tb_id, progress_cb=progress_cb)

        assert stats["pages"] == 4
        assert stats["ocr_pages"] == 4
        assert stats["is_scanned"] is True
        assert len(progress) > 0  # прогресс доходил до колбэка

        hits = await db.search_pages([tb_id], "РАСПОЗНАННЫЙ", limit=4)
        assert len(hits) == 4  # все страницы прошли через OCR и попали в индекс

    asyncio.run(run())


def test_unregistered_files(tmp_path: Path, db: Database):
    async def run():
        service = _service(tmp_path, db)
        class_id = await db.add_class("9А")
        subj = await db.add_subject(class_id, "Алгебра")
        (tmp_path / "textbooks" / "a.pdf").write_bytes(b"stub")
        (tmp_path / "textbooks" / "b.pdf").write_bytes(b"stub")
        (tmp_path / "textbooks" / "notes.txt").write_text("не pdf")

        files = unregistered_files(service.cfg, set())
        assert [f.name for f in files] == ["a.pdf", "b.pdf"]

        await db.add_textbook(subj, "a.pdf", "a")
        files = unregistered_files(service.cfg, {"a.pdf"})
        assert [f.name for f in files] == ["b.pdf"]

    asyncio.run(run())

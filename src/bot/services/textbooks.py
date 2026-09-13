"""Учебники: сканирование папки, инжест PDF, полнотекстовый поиск.

Пайплайн инжеста:
1. PDF лежит в data/textbooks/ (кладётся по SSH).
2. PyMuPDF извлекает текст каждой страницы.
3. Пустые страницы считаются сканами: если их > 30% — книга помечается
   is_scanned, и они прогоняются через vision-OCR (страница → PNG → модель).
4. Всё складывается в pages + FTS5-индекс.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from bot.config import Config
from bot.db.repo import Database
from bot.services.llm.client import LLMClient
from bot.services.ocr import ocr_image

log = logging.getLogger(__name__)

MIN_PAGE_CHARS = 30  # меньше — считаем страницу «пустой» (скан)
SCANNED_RATIO = 0.3
RENDER_DPI = 150
OCR_CONCURRENCY = 3


def unregistered_files(cfg: Config, known_filenames: set[str]) -> list[Path]:
    """PDF в папке textbooks, которых ещё нет в БД."""
    if not cfg.textbooks_dir.exists():
        return []
    out = []
    for p in sorted(cfg.textbooks_dir.glob("*.pdf")):
        if p.name not in known_filenames:
            out.append(p)
    return out


def sanitize_filename(name: str) -> str:
    """Имя файла из Telegram → безопасное имя PDF (без путей и мусора)."""
    name = Path(name).name
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "book.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    if len(name) > 120:  # запас на суффиксы от unique_path
        name = name[:-4][:116] + ".pdf"
    return name


def unique_path(directory: Path, filename: str) -> Path:
    """Не перезаписывать существующие книги: book.pdf → book_1.pdf → ..."""
    path = directory / filename
    stem, suffix = path.stem, path.suffix
    i = 1
    while path.exists():
        path = directory / f"{stem}_{i}{suffix}"
        i += 1
    return path


def _page_to_jpeg(page: "fitz.Page") -> bytes:
    pix = page.get_pixmap(dpi=RENDER_DPI)
    return pix.tobytes("jpeg", jpg_quality=80)


class TextbookService:
    def __init__(self, cfg: Config, db: Database, llm: LLMClient) -> None:
        self.cfg = cfg
        self.db = db
        self.llm = llm
        self._tasks: dict[int, asyncio.Task] = {}

    async def ingest(
        self,
        pdf_path: Path,
        subject_id: int,
        textbook_id: int,
        progress_cb=None,  # async callable(done, total, ocr_pages)
    ) -> dict:
        """Инжест одного PDF. Возвращает статистику."""
        loop = asyncio.get_running_loop()

        def _extract():
            doc = fitz.open(pdf_path)
            pages: list[tuple[int, str | None]] = []
            for i, page in enumerate(doc):
                text = page.get_text().strip()
                pages.append((i + 1, text if len(text) >= MIN_PAGE_CHARS else None))
            doc.close()
            return pages

        pages = await loop.run_in_executor(None, _extract)
        total = len(pages)
        empty = [no for no, text in pages if text is None]
        is_scanned = bool(empty) and len(empty) / max(total, 1) > SCANNED_RATIO

        rows: list[tuple[int, str]] = []
        ocr_done = 0
        sem = asyncio.Semaphore(OCR_CONCURRENCY)

        async def _ocr_page(page_no: int, page: "fitz.Page") -> tuple[int, str]:
            nonlocal ocr_done
            async with sem:
                img = await loop.run_in_executor(None, _page_to_jpeg, page)
                text = await ocr_image(self.llm, img)
                ocr_done += 1
                if progress_cb and (ocr_done % 5 == 0 or ocr_done == len(empty)):
                    await progress_cb(ocr_done, len(empty), total)
                return page_no, text

        if is_scanned and empty:
            doc = fitz.open(pdf_path)
            try:
                tasks = [asyncio.create_task(_ocr_page(no, doc[no - 1])) for no in empty]
                ocr_rows = await asyncio.gather(*tasks)
            finally:
                doc.close()
            ocr_map = dict(ocr_rows)
            rows = [(no, text if text is not None else ocr_map.get(no, ""))
                    for no, text in pages]
        else:
            rows = [(no, text or "") for no, text in pages]

        await self.db.replace_pages(textbook_id, rows)
        await self.db.finish_textbook(
            textbook_id, pages=total, ocr_pages=len(empty), is_scanned=is_scanned
        )
        log.info("Инжест %s: %d стр. (OCR: %d)", pdf_path.name, total, len(empty))
        return {"pages": total, "ocr_pages": len(empty), "is_scanned": is_scanned}

    async def start_ingest_task(self, pdf_path: Path, subject_id: int, textbook_id: int) -> asyncio.Task:
        task = asyncio.create_task(
            self.ingest(pdf_path, subject_id, textbook_id)
        )
        self._tasks[textbook_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(textbook_id, None))
        return task

    async def search(self, textbook_ids: list[int], query: str, limit: int = 3) -> list[dict]:
        return await self.db.search_pages(textbook_ids, query, limit=limit)


def extract_references(text: str) -> list[str]:
    """Номера упражнений/страниц из текста — для поиска по учебнику."""
    refs = re.findall(
        r"(?:№\s*|упр(?:ажнение)?\s*|стр(?:аниц[аы]|\.?)\s*)(\d{1,4}[а-я]?)",
        text,
        re.IGNORECASE,
    )
    return list(dict.fromkeys(refs))

"""Textbooks: folder scan, PDF ingestion and full-text page search."""
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

MIN_PAGE_CHARS = 30
SCANNED_RATIO = 0.3
RENDER_DPI = 150
OCR_CONCURRENCY = 3


def unregistered_files(cfg: Config, known_filenames: set[str]) -> list[Path]:
    """Return PDFs in the textbooks folder that are not in the database yet."""
    if not cfg.textbooks_dir.exists():
        return []
    out = []
    for p in sorted(cfg.textbooks_dir.glob("*.pdf")):
        if p.name not in known_filenames:
            out.append(p)
    return out


def sanitize_filename(name: str) -> str:
    """Turn a Telegram file name into a safe PDF file name."""
    name = Path(name).name
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "book.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    if len(name) > 120:
        name = name[:-4][:116] + ".pdf"
    return name


def unique_path(directory: Path, filename: str) -> Path:
    """Return a path in the directory that does not overwrite existing files."""
    path = directory / filename
    stem, suffix = path.stem, path.suffix
    i = 1
    while path.exists():
        path = directory / f"{stem}_{i}{suffix}"
        i += 1
    return path


def extract_references(text: str) -> list[str]:
    """Extract exercise/page numbers mentioned in a text."""
    refs = re.findall(
        r"(?:№\s*|упр(?:ажнение)?\s*|стр(?:аниц[аы]|\.?)\s*)(\d{1,4}[а-я]?)",
        text,
        re.IGNORECASE,
    )
    return list(dict.fromkeys(refs))


def _page_to_jpeg(page: "fitz.Page") -> bytes:
    """Render a PDF page to JPEG bytes at the configured DPI."""
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
        progress_cb=None,
    ) -> dict:
        """Ingest one PDF: extract text, OCR empty pages, rebuild the index."""
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
            """OCR a single rendered page under the concurrency semaphore."""
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
        log.info("Ingested %s: %d pages (OCR: %d)", pdf_path.name, total, len(empty))
        return {"pages": total, "ocr_pages": len(empty), "is_scanned": is_scanned}

    async def search(self, textbook_ids: list[int], query: str, limit: int = 3) -> list[dict]:
        """Run a full-text search over the given textbooks' pages."""
        return await self.db.search_pages(textbook_ids, query, limit=limit)

"""Тесты безопасных имён файлов для книг, загруженных через ТГ."""
from pathlib import Path

from bot.services.textbooks import sanitize_filename, unique_path


def test_sanitize_strips_paths_and_tricks():
    for raw in [
        "../../etc/passwd.pdf",
        "C:\\Users\\egor\\algebra.pdf",
        "Книга: алгебра *9*?.pdf",
    ]:
        out = sanitize_filename(raw)
        assert out.endswith(".pdf")
        assert not any(ch in out for ch in '\\/:*?"<>|')
    assert sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert sanitize_filename("C:\\Users\\egor\\algebra.pdf") == "algebra.pdf"


def test_sanitize_adds_pdf_extension():
    assert sanitize_filename("Книга по физике") == "Книга по физике.pdf"
    assert sanitize_filename("") == "book.pdf"


def test_sanitize_caps_length():
    long = "а" * 300 + ".pdf"
    out = sanitize_filename(long)
    assert len(out) == 116 + 4 and out.endswith(".pdf")


def test_unique_path_does_not_overwrite(tmp_path: Path):
    first = unique_path(tmp_path, "book.pdf")
    first.write_bytes(b"1")
    assert first == tmp_path / "book.pdf"

    second = unique_path(tmp_path, "book.pdf")
    assert second == tmp_path / "book_1.pdf"
    second.write_bytes(b"2")
    assert first.read_bytes() == b"1"

    third = unique_path(tmp_path, "book.pdf")
    assert third == tmp_path / "book_2.pdf"

"""Regression tests: exact-match callbacks must outrun prefix-match ones."""
from bot.handlers import admin


def _cb_names() -> list[str]:
    """Return handler function names in the callback_query registration order."""
    return [h.callback.__name__ for h in admin.router.callback_query.handlers]


def test_upload_handler_beats_prefix_handler():
    """The upload button must be caught by its exact handler, not the prefix one."""
    names = _cb_names()
    assert "adm_book_upload" in names and "adm_book_pick" in names
    assert names.index("adm_book_upload") < names.index("adm_book_pick")


def test_mode_exact_before_mode_prefix():
    """adm:mode must resolve to the switcher, not the setter."""
    names = _cb_names()
    assert names.index("adm_mode") < names.index("adm_mode_set")


def test_class_add_before_class_prefix():
    """adm:cls:add must resolve to the add-prompt, not the subject list."""
    names = _cb_names()
    assert names.index("adm_class_add") < names.index("adm_class_subjects")

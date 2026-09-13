"""Rendering tests: LaTeX to Unicode conversion and HTML tag passthrough."""
from bot.utils.tg_helpers import latex_to_unicode, md_to_html


def test_latex_fraction_and_power():
    """Convert \frac and ^{} LaTeX to readable Unicode."""
    out = latex_to_unicode("$\\frac{a}{125}^{-1/3}$")
    assert "$" not in out and "\\frac" not in out
    assert "a/125" in out and "⁻¹ᐟ³" in out


def test_latex_power_superscript():
    """Superscripts map to Unicode characters."""
    out = latex_to_unicode("$x^{2/3} + 2^3$")
    assert "x²ᐟ³" in out and "2³" in out


def test_latex_sqrt_and_cdot():
    """Sqrt and cdot convert to √ and ·."""
    out = latex_to_unicode("$\\sqrt{a} \\cdot 5$")
    assert "√" in out and "·" in out and "\\" not in out


def test_plain_text_untouched():
    """Text without LaTeX stays unchanged."""
    assert latex_to_unicode("просто текст без формул") == "просто текст без формул"


def test_md_to_html_renders_model_html_tags():
    """Model-emitted <b> tags must become real bold, not escaped text."""
    out = md_to_html("<b>Что я понял</b> по алгебре")
    assert "<b>Что я понял</b>" in out
    assert "&lt;b&gt;" not in out

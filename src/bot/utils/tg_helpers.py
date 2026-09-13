"""Telegram helpers: long-text splitting, markdown-to-HTML, LaTeX to Unicode, streaming."""
from __future__ import annotations

import html
import logging
import re
import time
from typing import AsyncIterator

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

log = logging.getLogger(__name__)

TG_LIMIT = 4096
SPLIT_LIMIT = 3900

_SUPERSCRIPT = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
    "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "−": "⁻",
    "(": "⁽", ")": "⁾", "/": "ᐟ", "=": "⁼", "n": "ⁿ", "a": "ᵃ",
    "b": "ᵇ", "k": "ᵏ", "m": "ᵐ", "x": "ˣ", "i": "ⁱ",
}
_SUBSCRIPT = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅",
    "6": "₆", "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "−": "₋",
}
_LATEX_COMMANDS = [
    ("\\left", ""), ("\\right", ""), ("\\!", ""), ("\\,", " "), ("\\;", " "),
    ("\\cdot", "·"), ("\\times", "×"), ("\\div", ":"), ("\\pm", "±"),
    ("\\approx", "≈"), ("\\neq", "≠"), ("\\le", "≤"), ("\\ge", "≥"),
    ("\\sqrt", "√"), ("\\pi", "π"), ("\\alpha", "α"), ("\\beta", "β"),
    ("\\gamma", "γ"), ("\\infty", "∞"), ("\\log", "log"), ("\\ln", "ln"),
    ("\\sin", "sin"), ("\\cos", "cos"), ("\\tg", "tg"), ("\\dfrac", "\\frac"),
    ("\\tfrac", "\\frac"),
]


def _to_script(content: str, table: dict[str, str]) -> str:
    """Map each character of content to its superscript/subscript variant."""
    out: list[str] = []
    for ch in content:
        out.append(table.get(ch, ch))
    return "".join(out)


def latex_to_unicode(text: str) -> str:
    """Convert $...$ LaTeX fragments to plain Unicode that Telegram renders well."""

    def _convert_expr(expr: str) -> str:
        for cmd, rep in _LATEX_COMMANDS:
            expr = expr.replace(cmd, rep)
        frac = re.compile(r"\\frac\{([^{}]*)\}\{([^{}]*)\}")
        while True:
            new = frac.sub(lambda m: f"{m.group(1)}/{m.group(2)}", expr)
            if new == expr:
                break
            expr = new
        power = re.compile(r"\^\{([^{}]*)\}")
        while True:
            new = power.sub(lambda m: _to_script(m.group(1), _SUPERSCRIPT), expr)
            if new == expr:
                break
            expr = new
        expr = re.sub(r"\^([0-9a-zA-Z])", lambda m: _to_script(m.group(1), _SUPERSCRIPT), expr)
        index = re.compile(r"_\{([^{}]*)\}")
        while True:
            new = index.sub(lambda m: _to_script(m.group(1), _SUBSCRIPT), expr)
            if new == expr:
                break
            expr = new
        expr = re.sub(r"_([0-9a-zA-Z])", lambda m: _to_script(m.group(1), _SUBSCRIPT), expr)
        expr = re.sub(r"\{([^{}]*)\}", r"\1", expr)
        return expr

    def _convert_dollar(m: re.Match) -> str:
        return _convert_expr(m.group(1))

    return re.sub(r"\$([^$\n]+)\$", _convert_dollar, text)


def split_text(text: str, limit: int = SPLIT_LIMIT) -> list[str]:
    """Split long text into chunks under the limit on paragraph/sentence borders."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"))
        if cut < limit // 2:
            cut = window.rfind(". ")
            if cut < limit // 2:
                cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


def md_to_html(text: str) -> str:
    """Convert markdown/LaTeX model output to Telegram HTML without dependencies."""
    html_tags: list[str] = []
    code_blocks: list[str] = []

    def _stash_code(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    def _stash_tag(m: re.Match) -> str:
        html_tags.append(m.group(0))
        return f"\x00HT{len(html_tags) - 1}\x00"

    text = re.sub(r"```[a-zA-Z0-9]*\n?(.*?)```", _stash_code, text, flags=re.S)
    # the model is allowed to emit Telegram HTML tags directly (<b>...</b>)
    text = re.sub(r"</?(?:b|i|u|s|code|pre|u)[^>]*>", _stash_tag, text, flags=re.I)

    text = html.escape(text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.M)
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.M)

    def _unstash_code(m: re.Match) -> str:
        return "<pre>" + html.escape(code_blocks[int(m.group(1))]) + "</pre>"

    def _unstash_tag(m: re.Match) -> str:
        return html_tags[int(m.group(1))]

    text = re.sub(r"\x00CB(\d+)\x00", _unstash_code, text)
    return re.sub(r"\x00HT(\d+)\x00", _unstash_tag, text)


class StreamEditor:
    """Stream an LLM response into one Telegram message with throttled edits."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id: int | None = None
        self._last_edit = 0.0
        self._last_len = 0

    async def start(self, placeholder: str = "🤔 Думаю…") -> None:
        """Send the placeholder message that will be edited while streaming."""
        msg = await self.bot.send_message(self.chat_id, placeholder)
        self.message_id = msg.message_id

    async def _edit(self, text: str) -> None:
        """Edit the streaming message, falling back to a new one if uneditable."""
        if not self.message_id:
            return
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.message_id, text=text
            )
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
            if "message can't be edited" in str(e).lower() or "to edit" in str(e).lower():
                msg = await self.bot.send_message(self.chat_id, text)
                self.message_id = msg.message_id
            else:
                log.debug("edit_message_text: %s", e)

    async def push(self, full_text: str) -> None:
        """Edit the message with the accumulated text if the throttle allows."""
        now = time.monotonic()
        if len(full_text) - self._last_len < 80 and now - self._last_edit < 1.2:
            return
        if now - self._last_edit < 0.3:
            return
        self._last_edit = now
        self._last_len = len(full_text)
        shown = full_text[:TG_LIMIT - 20] + ("…" if len(full_text) > TG_LIMIT - 20 else "")
        await self._edit(shown or "…")

    async def finish(self, full_text: str) -> None:
        """Set the final formatted text, sending the overflow as new messages."""
        if not full_text.strip():
            full_text = "(пустой ответ модели)"
        formatted = md_to_html(latex_to_unicode(full_text))
        chunks = split_text(formatted) or ["(пусто)"]
        await self._edit(chunks[0][:TG_LIMIT - 1] + "…"
                         if len(chunks) > 1 else chunks[0])
        first = chunks[0]
        while len(first) > TG_LIMIT:
            await self.bot.send_message(self.chat_id, first[TG_LIMIT:])
            first = first[:TG_LIMIT]
        for chunk in chunks[1:]:
            while len(chunk) > TG_LIMIT:
                await self.bot.send_message(self.chat_id, chunk[:TG_LIMIT])
                chunk = chunk[TG_LIMIT:]
            await self.bot.send_message(self.chat_id, chunk)


async def stream_to_telegram(
    bot: Bot,
    chat_id: int,
    deltas: AsyncIterator[str],
    placeholder: str = "🤔 Думаю…",
) -> str:
    """Collect a full answer from a delta stream while showing it to the user."""
    editor = StreamEditor(bot, chat_id)
    await editor.start(placeholder)
    parts: list[str] = []
    async for delta in deltas:
        parts.append(delta)
        await editor.push("".join(parts))
    text = "".join(parts)
    await editor.finish(text)
    return text

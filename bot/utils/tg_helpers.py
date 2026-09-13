"""Хелперы Telegram: разбивка длинных текстов, md→HTML, стриминг ответа."""
from __future__ import annotations

import html
import re
import time
from typing import AsyncIterator

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

TG_LIMIT = 4096
SPLIT_LIMIT = 3900  # с запасом на разметку


def split_text(text: str, limit: int = SPLIT_LIMIT) -> list[str]:
    """Режет длинный текст на куски ≤ limit по границам абзацев/предложений."""
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
    """Мини-конвертер markdown → Telegram HTML (без внешних зависимостей)."""
    # блоки кода сначала, плейсхолдеры
    code_blocks: list[str] = []

    def _stash(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[a-zA-Z0-9]*\n?(.*?)```", _stash, text, flags=re.S)
    text = html.escape(text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.M)
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.M)

    def _unstash(m: re.Match) -> str:
        return "<pre>" + html.escape(code_blocks[int(m.group(1))]) + "</pre>"

    return re.sub(r"\x00CB(\d+)\x00", _unstash, text)


class StreamEditor:
    """Стримит LLM-поток в одно сообщение TG: edit не чаще ~1 раза в 1.2с
    и не меньше чем на 80 символов. Финал — с HTML-разметкой и разбиением."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id: int | None = None
        self._last_edit = 0.0
        self._last_len = 0

    async def start(self, placeholder: str = "🤔 Думаю…") -> None:
        msg = await self.bot.send_message(self.chat_id, placeholder)
        self.message_id = msg.message_id

    async def _edit(self, text: str) -> None:
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
                log = __import__("logging").getLogger(__name__)
                log.debug("edit_message_text: %s", e)

    async def push(self, full_text: str) -> None:
        now = time.monotonic()
        if len(full_text) - self._last_len < 80 and now - self._last_edit < 1.2:
            return
        if now - self._last_edit < 0.3:  # жёсткий пол
            return
        self._last_edit = now
        self._last_len = len(full_text)
        shown = full_text[:TG_LIMIT - 20] + ("…" if len(full_text) > TG_LIMIT - 20 else "")
        await self._edit(shown or "…")

    async def finish(self, full_text: str) -> None:
        """Финальный текст с разметкой; хвост — отдельными сообщениями."""
        if not full_text.strip():
            full_text = "(пустой ответ модели)"
        formatted = md_to_html(full_text)
        chunks = split_text(formatted) or ["(пусто)"]
        await self._edit(chunks[0][:TG_LIMIT - 1] + "…"
                         if len(chunks) > 1 else chunks[0])
        # если отформатированный кусок всё равно длиннее лимита — режем ещё раз
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
    """Собирает весь текст из потока дельт, параллельно показывая его юзеру."""
    editor = StreamEditor(bot, chat_id)
    await editor.start(placeholder)
    parts: list[str] = []
    async for delta in deltas:
        parts.append(delta)
        await editor.push("".join(parts))
    text = "".join(parts)
    await editor.finish(text)
    return text

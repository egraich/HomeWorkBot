"""Middlewares: update logging, whitelist access control and anti-flood."""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, User

from bot.config import Config

log = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    """Log every incoming update with the handler name and execution time."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        """Measure the handler run and write one structured line per update."""
        user: User | None = data.get("event_from_user")
        start = time.monotonic()
        try:
            result = await handler(event, data)
        except Exception:
            log.error(
                "handler failed after %.2fs (user=%s)",
                time.monotonic() - start, getattr(user, "id", "?"),
                exc_info=True,
            )
            raise
        log.info(
            "handled %s in %.2fs (user=%s)",
            type(event).__name__, time.monotonic() - start,
            getattr(user, "id", "?"),
        )
        return result


class AuthMiddleware(BaseMiddleware):
    """Allow only tg_ids listed in ADMIN_IDS/ALLOWED_IDS."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        """Reject events from users outside the whitelist."""
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        if not self.cfg.is_allowed(user.id):
            log.warning(
                "unauthorized access attempt: id=%s username=%s",
                user.id, user.username,
            )
            if isinstance(event, CallbackQuery):
                await event.answer("🔒 Приватный бот.", show_alert=True)
            else:
                await event.answer("🔒 Приватный бот. Тебя нет в whitelist.")
            return None
        return await handler(event, data)


class ThrottleMiddleware(BaseMiddleware):
    """Silently drop messages faster than one per second per chat."""

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        """Drop the event if it arrives within the throttle interval."""
        chat_id = event.chat.id if event.chat else 0
        now = time.monotonic()
        if now - self._last.get(chat_id, 0.0) < self.interval:
            log.info("throttled message from chat=%s", chat_id)
            return None
        self._last[chat_id] = now
        return await handler(event, data)

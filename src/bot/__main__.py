"""Entry point: python -m bot"""
from __future__ import annotations

import asyncio
import logging
import sys

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.types import BotCommand
from openai import AsyncOpenAI

from bot.config import Config
from bot.db.repo import Database
from bot.handlers import ALL_ROUTERS
from bot.middlewares import AuthMiddleware, LoggingMiddleware, ThrottleMiddleware
from bot.services.container import Services
from bot.services.llm.catalog import ModelCatalog
from bot.services.llm.client import LLMClient
from bot.services.llm.router import Router
from bot.services.llm.usage import Usage
from bot.services.textbooks import TextbookService
from bot.utils.logging_setup import setup_logging

log = logging.getLogger("bot")


async def main() -> None:
    """Wire all services together and start long polling."""
    cfg = Config()
    setup_logging(cfg.log_level)
    cfg.ensure_dirs()
    if not cfg.bot_token:
        sys.exit("BOT_TOKEN пуст — заполни .env (как получить токен — в README)")
    if not cfg.hackclub_api_key:
        sys.exit("HACKCLUB_API_KEY пуст — заполни .env")

    db = Database(cfg.db_path)
    await db.connect()

    openai_client = AsyncOpenAI(
        api_key=cfg.hackclub_api_key,
        base_url=cfg.llm_base_url,
        timeout=httpx.Timeout(180.0, connect=15.0),
        max_retries=0,
    )
    catalog = ModelCatalog(openai_client)
    await catalog.refresh()

    usage = Usage(db, cfg, catalog)
    router = Router(catalog)
    llm = LLMClient(cfg, db, catalog, router, usage)
    textbooks = TextbookService(cfg, db, llm)
    services = Services(
        cfg=cfg, db=db, catalog=catalog, router=router, usage=usage,
        llm=llm, textbooks=textbooks,
    )

    tg_session = None
    if cfg.tg_api_base:
        # Local Bot API servers (TELEGRAM_LOCAL=1) return absolute file paths
        # inside their own container; is_local=True makes aiogram read files
        # from disk instead of HTTP. The data dir must therefore be mounted
        # into this container at the same path (see docker-compose.yml).
        tg_session = AiohttpSession(
            api=TelegramAPIServer.from_base(cfg.tg_api_base, is_local=True)
        )
    bot = Bot(
        cfg.bot_token,
        session=tg_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp["services"] = services
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())
    dp.message.middleware(AuthMiddleware(cfg))
    dp.callback_query.middleware(AuthMiddleware(cfg))
    dp.message.middleware(ThrottleMiddleware())
    dp.include_routers(*ALL_ROUTERS)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Меню"),
            BotCommand(command="plan", description="Составить план домашки"),
            BotCommand(command="stop", description="Завершить сессию"),
            BotCommand(command="reset", description="Сбросить сессию"),
            BotCommand(command="style", description="Стиль сочинений"),
            BotCommand(command="admin", description="Админ-панель"),
            BotCommand(command="help", description="Помощь"),
        ]
    )

    mode = await services.llm.current_mode()
    log.info(
        "Starting: mode=%s admins=%d whitelist=%d catalog_models=%d log_level=%s",
        mode, len(cfg.admin_id_list), len(cfg.allowed_id_list),
        len(catalog._models), cfg.log_level,
    )
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    except TelegramConflictError:
        log.error(
            "TelegramConflictError: another bot instance is polling with this "
            "token. Stop the other instance (check your own machine too) and "
            "restart the container."
        )
        raise SystemExit(1)
    finally:
        await db.close()
        log.info("Stopped")


if __name__ == "__main__":
    asyncio.run(main())

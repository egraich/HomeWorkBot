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
from aiogram.types import BotCommand
from openai import AsyncOpenAI

from bot.config import Config
from bot.db.repo import Database
from bot.handlers import ALL_ROUTERS
from bot.middlewares import AuthMiddleware, ThrottleMiddleware
from bot.services.container import Services
from bot.services.llm.catalog import ModelCatalog
from bot.services.llm.client import LLMClient
from bot.services.llm.router import Router
from bot.services.llm.usage import Usage
from bot.services.textbooks import TextbookService

log = logging.getLogger("bot")


async def main() -> None:
    """Wire all services together and start long polling."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    cfg = Config()
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
        tg_session = AiohttpSession(
            api=TelegramAPIServer.from_base(cfg.tg_api_base)
        )
    bot = Bot(
        cfg.bot_token,
        session=tg_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp["services"] = services
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
        "Стартую. Режим: %s | админов: %d | whitelist: %d | моделей в каталоге: %d",
        mode, len(cfg.admin_id_list), len(cfg.allowed_id_list), len(catalog._models),
    )
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await db.close()
        log.info("Остановлен")


if __name__ == "__main__":
    asyncio.run(main())

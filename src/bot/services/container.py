"""Контейнер сервисов — одна точка сборки зависимостей."""
from __future__ import annotations

from dataclasses import dataclass

from bot.config import Config
from bot.db.repo import Database
from bot.services.llm.catalog import ModelCatalog
from bot.services.llm.client import LLMClient
from bot.services.llm.router import Router
from bot.services.llm.usage import Usage
from bot.services.textbooks import TextbookService


@dataclass(slots=True)
class Services:
    cfg: Config
    db: Database
    catalog: ModelCatalog
    router: Router
    usage: Usage
    llm: LLMClient
    textbooks: TextbookService

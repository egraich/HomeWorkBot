"""Живой каталог моделей hackai: наличие, цены, поддержка vision.

Список и метаданные берутся с GET /proxy/v1/models (OpenRouter-формат):
у каждой модели есть pricing.prompt / pricing.completion (USD за 1M токенов)
и architecture.input_modalities (["text", "image"]).
"""
from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelInfo:
    id: str
    prompt_price: float = 0.0  # USD за 1M токенов
    completion_price: float = 0.0
    vision: bool = False


class ModelCatalog:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client
        self._models: dict[str, ModelInfo] = {}

    async def refresh(self) -> int:
        """Подтянуть список моделей. Возвращает количество. Не падает никогда:
        при ошибке каталог остаётся пустым, роутер работает по конфигу."""
        try:
            page = await self._client.models.list()
        except Exception as e:  # noqa: BLE001 — сеть может быть недоступна
            log.warning("Не удалось получить каталог моделей: %s", e)
            return len(self._models)
        models: dict[str, ModelInfo] = {}
        for m in page.data:
            extra = getattr(m, "model_extra", None) or {}
            pricing = extra.get("pricing") or {}
            arch = extra.get("architecture") or {}
            modalities = arch.get("input_modalities") or ["text"]
            try:
                prompt_price = float(pricing.get("prompt") or 0) * 1_000_000
                completion_price = float(pricing.get("completion") or 0) * 1_000_000
            except (TypeError, ValueError):
                prompt_price = completion_price = 0.0
            models[m.id] = ModelInfo(
                id=m.id,
                prompt_price=prompt_price,
                completion_price=completion_price,
                vision="image" in modalities,
            )
        self._models = models
        log.info("Каталог моделей обновлён: %d моделей", len(models))
        return len(models)

    def exists(self, model_id: str) -> bool:
        if not self._models:
            return True  # каталог недоступен — доверяем конфигу
        return model_id in self._models

    def get(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def is_vision(self, model_id: str) -> bool:
        info = self._models.get(model_id)
        if info:
            return info.vision
        return True  # без каталога считаем, что умеет (клиент разберётся)

    def is_free(self, model_id: str) -> bool:
        if model_id.endswith(":free"):
            return True
        info = self._models.get(model_id)
        return bool(info and info.prompt_price == 0 and info.completion_price == 0)

    def cost_usd(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        info = self._models.get(model_id)
        if not info:
            return 0.0
        return (
            prompt_tokens / 1_000_000 * info.prompt_price
            + completion_tokens / 1_000_000 * info.completion_price
        )

    def closest(self, model_id: str, n: int = 3) -> list[str]:
        """Похожие существующие ID — для отладки роутинга."""
        if not self._models:
            return []
        return difflib.get_close_matches(model_id, list(self._models), n=n, cutoff=0.45)

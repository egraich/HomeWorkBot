"""Роутинг: задача × режим качества → список моделей-кандидатов.

Таблица ROUTING правится без кода — это обычный словарь.
ID моделей в OpenRouter-стиле, как их отдаёт GET /proxy/v1/models у hackai.

extra — доп. параметры запроса. Важно: у z-ai/glm-5.3 reasoning выключить
нельзя (API отвечает 400), поэтому для него extra не ставим и просто даём
модели достаточно max_tokens, чтобы «мысли» влезли до ответа.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bot.config import QUALITY_MODES
from bot.services.llm.catalog import ModelCatalog

Task = str  # quick | ocr | brain | writer

NO_THINKING = {"reasoning": {"enabled": False}}


@dataclass(slots=True, frozen=True)
class ModelSpec:
    model: str
    vision: bool = False
    extra: dict = field(default_factory=dict)


# Основные модели по режимам.
ROUTING: dict[str, dict[Task, ModelSpec]] = {
    "econ": {
        "quick": ModelSpec("inclusionai/ling-3.0-flash-vl:free", vision=True, extra=NO_THINKING),
        "ocr": ModelSpec("qwen/qwen3.8-flash", vision=True),
        "brain": ModelSpec("deepseek/deepseek-v4.1-flash"),
        # glm-5.3 не берём писателем: обязательный reasoning нестабильно
        # влезает в лимиты (смоук-тест: пустой content), deepseek надёжен и дешев
        "writer": ModelSpec("deepseek/deepseek-v4.1-flash"),
    },
    "medium": {
        "quick": ModelSpec("qwen/qwen3.8-flash", vision=True, extra=NO_THINKING),
        "ocr": ModelSpec("google/gemini-3.8-flash", vision=True),
        "brain": ModelSpec("deepseek/deepseek-v4-pro-0813"),
        # qwen3.8-max думает долго: писателю хватает дефолтного лимита 3500,
        # при меньшем cap отдаёт пустой content (проверено живьём)
        "writer": ModelSpec("qwen/qwen3.8-max-0902"),
    },
    "max": {
        "quick": ModelSpec("qwen/qwen3.8-flash", vision=True, extra=NO_THINKING),
        "ocr": ModelSpec("google/gemini-3.8-flash", vision=True),
        "brain": ModelSpec("openai/gpt-6-astra", vision=True),
        # claude-fable-5.1 не используем: шлюз отдаёт 404 "guardrail settings"
        # (проверено 2026-09-13 живым вызовом). Лучший работающий стилист — Terra
        "writer": ModelSpec("~openai/gpt-terra-latest"),
    },
}

# Фолбэки, если основная модель недоступна/удалена из каталога/ошибнулась.
# Первыми — похожие по классу, последней — самая сильная (для сложных задач).
FALLBACKS: dict[Task, list[str]] = {
    "quick": ["qwen/qwen3.8-flash", "google/gemini-3.8-flash"],
    "ocr": ["google/gemini-3.8-flash", "qwen/qwen3.8-max-0902", "openai/gpt-6-astra"],
    "brain": [
        "z-ai/glm-5.3",
        "~openai/gpt-terra-latest",
        "deepseek/deepseek-v4-pro-0813",
        "openai/gpt-6-astra",
    ],
    "writer": ["~openai/gpt-terra-latest", "qwen/qwen3.8-max-0902",
               "z-ai/glm-5.3", "deepseek/deepseek-v4.1-flash"],
}


class Router:
    def __init__(self, catalog: ModelCatalog) -> None:
        self.catalog = catalog

    def spec(self, mode: str, task: Task) -> ModelSpec:
        return ROUTING.get(mode, ROUTING["econ"])[task]

    def candidates(self, mode: str, task: Task) -> list[str]:
        """Упорядоченный список кандидатов: основная модель режима + фолбэки.
        Модели, отсутствующие в живом каталоге, выкидываются (если каталог получен)."""
        primary = self.spec(mode, task).model
        seen: list[str] = []
        for model_id in [primary, *FALLBACKS.get(task, [])]:
            if model_id not in seen and self.catalog.exists(model_id):
                seen.append(model_id)
        return seen or [primary]

    def extra_for(self, mode: str, task: Task, model_id: str) -> dict:
        """extra-параметры применимы только к основной модели роли
        (фолбэки могут её не принимать — например glm запрещает reasoning off)."""
        if self.spec(mode, task).model == model_id:
            return self.spec(mode, task).extra
        return {}

    def primary_vision(self, mode: str, task: Task) -> bool:
        spec = self.spec(mode, task)
        return spec.vision or self.catalog.is_vision(spec.model)


def all_routed_ids() -> list[str]:
    """Все ID, упомянутые в ROUTING — для смоук-теста."""
    ids: list[str] = []
    for mode in QUALITY_MODES:
        for spec in ROUTING[mode].values():
            if spec.model not in ids:
                ids.append(spec.model)
    return ids

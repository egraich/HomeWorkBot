"""Model routing: task x quality mode -> ordered candidate list."""
from __future__ import annotations

from dataclasses import dataclass, field

from bot.config import QUALITY_MODES
from bot.services.llm.catalog import ModelCatalog

Task = str

NO_THINKING = {"reasoning": {"enabled": False}}


@dataclass(slots=True, frozen=True)
class ModelSpec:
    """Primary model of a role with its vision flag and extra request params."""

    model: str
    vision: bool = False
    extra: dict = field(default_factory=dict)


ROUTING: dict[str, dict[Task, ModelSpec]] = {
    "econ": {
        "quick": ModelSpec("inclusionai/ling-3.0-flash-vl:free", vision=True, extra=NO_THINKING),
        "ocr": ModelSpec("qwen/qwen3.8-flash", vision=True),
        "brain": ModelSpec("deepseek/deepseek-v4.1-flash"),
        "writer": ModelSpec("deepseek/deepseek-v4.1-flash"),
    },
    "medium": {
        "quick": ModelSpec("qwen/qwen3.8-flash", vision=True, extra=NO_THINKING),
        "ocr": ModelSpec("google/gemini-3.8-flash", vision=True),
        "brain": ModelSpec("deepseek/deepseek-v4-pro-0813"),
        "writer": ModelSpec("qwen/qwen3.8-max-0902"),
    },
    "max": {
        "quick": ModelSpec("qwen/qwen3.8-flash", vision=True, extra=NO_THINKING),
        "ocr": ModelSpec("google/gemini-3.8-flash", vision=True),
        "brain": ModelSpec("openai/gpt-6-astra", vision=True),
        "writer": ModelSpec("~openai/gpt-terra-latest"),
    },
}

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
        """Return the primary model spec for a mode and task."""
        return ROUTING.get(mode, ROUTING["econ"])[task]

    def candidates(self, mode: str, task: Task) -> list[str]:
        """Return ordered candidates: the mode primary followed by fallbacks."""
        primary = self.spec(mode, task).model
        seen: list[str] = []
        for model_id in [primary, *FALLBACKS.get(task, [])]:
            if model_id not in seen and self.catalog.exists(model_id):
                seen.append(model_id)
        return seen or [primary]

    def extra_for(self, mode: str, task: Task, model_id: str) -> dict:
        """Return extra request params, applying them only to the primary model."""
        if self.spec(mode, task).model == model_id:
            return self.spec(mode, task).extra
        return {}

    def primary_vision(self, mode: str, task: Task) -> bool:
        """Return True if the primary model of a role accepts images."""
        spec = self.spec(mode, task)
        return spec.vision or self.catalog.is_vision(spec.model)


def all_routed_ids() -> list[str]:
    """Return every model id mentioned in ROUTING, for the smoke test."""
    ids: list[str] = []
    for mode in QUALITY_MODES:
        for spec in ROUTING[mode].values():
            if spec.model not in ids:
                ids.append(spec.model)
    return ids

"""Единая точка вызова LLM: роутинг по задачам, фолбэки, бюджет, usage.

Все вызовы модели в боте идут через LLMClient.chat / chat_stream.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from openai import AsyncOpenAI, APIError, BadRequestError, NotFoundError, RateLimitError

from bot.config import Config
from bot.db.repo import Database
from bot.services.llm.catalog import ModelCatalog
from bot.services.llm.router import Router
from bot.services.llm.usage import BudgetExceeded, Usage  # реэкспорт для хендлеров

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = {"quick": 300, "ocr": 3000, "brain": 4500, "writer": 3500}
# У «думающих» моделей reasoning съедает токены до content — маленькие лимиты
# дают пустой ответ. Поэтому лимиты щедрые, а не впритык.


class LLMError(Exception):
    """Все кандидаты провалились."""


@dataclass(slots=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


def _estimate_tokens(messages: list[dict]) -> int:
    total = sum(len(str(m.get("content", ""))) for m in messages)
    return max(1, total // 4)


class LLMClient:
    def __init__(
        self,
        cfg: Config,
        db: Database,
        catalog: ModelCatalog,
        router: Router,
        usage: Usage,
    ) -> None:
        self.cfg = cfg
        self.db = db
        self.catalog = catalog
        self.router = router
        self.usage = usage
        self._client = AsyncOpenAI(
            api_key=cfg.hackclub_api_key,
            base_url=cfg.llm_base_url,
            timeout=httpx.Timeout(180.0, connect=15.0),
            max_retries=0,  # ретраи и фолбэки — свои
        )

    async def current_mode(self) -> str:
        mode = await self.db.get_setting("quality_mode", self.cfg.default_mode)
        return mode if mode in ("econ", "medium", "max") else self.cfg.default_mode

    async def _candidates(self, task: str) -> list[str]:
        mode = await self.current_mode()
        return self.router.candidates(mode, task)

    # --- обычный вызов ----------------------------------------------------

    async def chat(
        self,
        task: str,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        max_tokens = max_tokens or DEFAULT_MAX_TOKENS.get(task)
        mode = await self.current_mode()
        budget_blocked = True
        for model_id in await self._candidates(task):
            if not await self.usage.allows(model_id):
                log.info("Бюджет исчерпан, пропуск платной модели %s", model_id)
                continue
            budget_blocked = False
            extra = self.router.extra_for(mode, task, model_id)
            try:
                resp = await self._client.chat.completions.create(
                    model=model_id,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=extra or None,
                )
            except (NotFoundError, BadRequestError) as e:
                log.warning("Модель %s не приняла запрос (%s), пробую следующую", model_id, e)
                continue
            except RateLimitError:
                log.warning("429 у %s, пауза 5с и повтор", model_id)
                await asyncio.sleep(5)
                continue
            except APIError as e:
                log.warning("Ошибка API у %s: %s", model_id, e)
                continue
            text = resp.choices[0].message.content or ""
            pt = ct = 0
            if resp.usage:
                pt, ct = resp.usage.prompt_tokens or 0, resp.usage.completion_tokens or 0
            else:
                pt, ct = _estimate_tokens(messages), max(1, len(text) // 4)
            await self.usage.record(model_id, task, pt, ct)
            if not text.strip():
                # reasoning-модель сожрала лимит и не ответила — пробуем следующую
                log.warning("Модель %s вернула пустой content, пробую следующую", model_id)
                continue
            cost = self.catalog.cost_usd(model_id, pt, ct)
            return LLMResult(text=text, model=model_id, prompt_tokens=pt,
                             completion_tokens=ct, cost_usd=cost)
        if budget_blocked:
            raise BudgetExceeded(
                "Дневной бюджет hackai исчерпан — сброс в 00:00 UTC (03:00 МСК). "
                "Квитанции на бесплатной модели продолжат работать."
            )
        raise LLMError("Ни одна модель не ответила — попробуй ещё раз.")

    # --- стриминг ----------------------------------------------------------

    async def chat_stream(
        self,
        task: str,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Асинхронный генератор дельт текста. Usage пишется в конце потока.

        Если модель упала до первой дельты — переключается на следующего
        кандидата; если в середине — просто завершает поток (текст частичный).
        """
        max_tokens = max_tokens or DEFAULT_MAX_TOKENS.get(task)
        pt_est = _estimate_tokens(messages)
        mode = await self.current_mode()
        budget_blocked = True
        for model_id in await self._candidates(task):
            if not await self.usage.allows(model_id):
                continue
            budget_blocked = False
            extra = self.router.extra_for(mode, task, model_id)
            try:
                stream = await self._client.chat.completions.create(
                    model=model_id,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                    extra_body=extra or None,
                )
            except BadRequestError:
                try:
                    stream = await self._client.chat.completions.create(
                        model=model_id,
                        messages=messages,  # type: ignore[arg-type]
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=True,
                    )
                except (NotFoundError, BadRequestError, RateLimitError) as e:
                    log.warning("Модель %s не стримит (%s), пробую следующую", model_id, e)
                    continue
            except (NotFoundError, RateLimitError) as e:
                log.warning("Модель %s: %s, пробую следующую", model_id, e)
                continue

            collected: list[str] = []
            pt = ct = 0
            try:
                async for chunk in stream:
                    if getattr(chunk, "usage", None):
                        pt = chunk.usage.prompt_tokens or 0
                        ct = chunk.usage.completion_tokens or 0
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        delta = chunk.choices[0].delta.content
                        collected.append(delta)
                        yield delta
            except APIError as e:
                log.warning("Поток %s оборвался: %s", model_id, e)

            if not collected:
                log.warning("Модель %s отдала пустой поток, пробую следующую", model_id)
                if pt or ct:
                    await self.usage.record(model_id, task, pt, ct)
                continue
            pt = pt or pt_est
            ct = ct or max(1, sum(map(len, collected)) // 4)
            await self.usage.record(model_id, task, pt, ct)
            return
        if budget_blocked:
            raise BudgetExceeded(
                "Дневной бюджет hackai исчерпан — сброс в 00:00 UTC (03:00 МСК)."
            )
        raise LLMError("Ни одна модель не ответила — попробуй ещё раз.")

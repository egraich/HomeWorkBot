"""Single entry point for every LLM call: task routing, fallbacks, budget."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from openai import AsyncOpenAI, APIError, BadRequestError, NotFoundError, RateLimitError

from bot.config import Config
from bot.db.repo import Database
from bot.services.llm.catalog import ModelCatalog
from bot.services.llm.router import Router
from bot.services.llm.usage import BudgetExceeded, Usage

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = {"quick": 300, "ocr": 3000, "brain": 4500, "writer": 3500}


class LLMError(Exception):
    """All model candidates failed."""


@dataclass(slots=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate (~4 chars per token) when usage is missing."""
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
            max_retries=0,
        )

    async def current_mode(self) -> str:
        """Return the quality mode currently set in settings."""
        mode = await self.db.get_setting("quality_mode", self.cfg.default_mode)
        return mode if mode in ("econ", "medium", "max") else self.cfg.default_mode

    async def _candidates(self, task: str) -> list[str]:
        """Return ordered model candidates for a task in the current mode."""
        mode = await self.current_mode()
        return self.router.candidates(mode, task)

    async def chat(
        self,
        task: str,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """Run a non-streaming completion, walking the fallback chain on failure."""
        max_tokens = max_tokens or DEFAULT_MAX_TOKENS.get(task)
        mode = await self.current_mode()
        budget_blocked = True
        for model_id in await self._candidates(task):
            if not await self.usage.allows(model_id):
                log.info("Budget exhausted, skipping paid model %s", model_id)
                continue
            budget_blocked = False
            extra = self.router.extra_for(mode, task, model_id)
            t0 = time.monotonic()
            try:
                resp = await self._client.chat.completions.create(
                    model=model_id,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=extra or None,
                )
            except (NotFoundError, BadRequestError) as e:
                log.warning("Model %s rejected the request (%s), trying next", model_id, e)
                continue
            except RateLimitError:
                log.warning("429 at %s, sleeping 5s and retrying", model_id)
                await asyncio.sleep(5)
                continue
            except APIError as e:
                log.warning("API error at %s: %s", model_id, e)
                continue
            text = resp.choices[0].message.content or ""
            pt = ct = 0
            if resp.usage:
                pt, ct = resp.usage.prompt_tokens or 0, resp.usage.completion_tokens or 0
            else:
                pt, ct = _estimate_tokens(messages), max(1, len(text) // 4)
            await self.usage.record(model_id, task, pt, ct)
            if not text.strip():
                log.warning("Model %s returned empty content, trying next", model_id)
                continue
            cost = self.catalog.cost_usd(model_id, pt, ct)
            log.info(
                "llm call ok: task=%s model=%s in %.1fs tokens=%d+%d cost=$%.5f",
                task, model_id, time.monotonic() - t0, pt, ct, cost,
            )
            return LLMResult(text=text, model=model_id, prompt_tokens=pt,
                             completion_tokens=ct, cost_usd=cost)
        if budget_blocked:
            raise BudgetExceeded(
                "Дневной бюджет hackai исчерпан — сброс в 00:00 UTC (03:00 МСК). "
                "Квитанции на бесплатной модели продолжат работать."
            )
        raise LLMError("Ни одна модель не ответила — попробуй ещё раз.")

    async def chat_stream(
        self,
        task: str,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas of a streamed completion with fallback handling."""
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
                    log.warning("Model %s can't stream (%s), trying next", model_id, e)
                    continue
            except (NotFoundError, RateLimitError) as e:
                log.warning("Model %s: %s, trying next", model_id, e)
                continue

            collected: list[str] = []
            pt = ct = 0
            t0 = time.monotonic()
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
                log.warning("Stream from %s broke: %s", model_id, e)

            if not collected:
                log.warning("Model %s produced an empty stream, trying next", model_id)
                if pt or ct:
                    await self.usage.record(model_id, task, pt, ct)
                continue
            pt = pt or pt_est
            ct = ct or max(1, sum(map(len, collected)) // 4)
            await self.usage.record(model_id, task, pt, ct)
            log.info(
                "llm stream ok: task=%s model=%s in %.1fs tokens=%d+%d cost=$%.5f",
                task, model_id, time.monotonic() - t0, pt, ct,
                self.catalog.cost_usd(model_id, pt, ct),
            )
            return
        if budget_blocked:
            raise BudgetExceeded(
                "Дневной бюджет hackai исчерпан — сброс в 00:00 UTC (03:00 МСК)."
            )
        raise LLMError("Ни одна модель не ответила — попробуй ещё раз.")

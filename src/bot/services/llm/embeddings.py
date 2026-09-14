"""Text embeddings via the gateway (semantic page search)."""
from __future__ import annotations

import logging

from openai import AsyncOpenAI

from bot.config import Config
from bot.services.llm.usage import Usage

log = logging.getLogger(__name__)

EMBED_MODEL = "qwen/qwen3-embedding-8b"
EMBED_COST_PER_M_TOKENS = 0.01  # observed live: $3.3e-07 for 33 tokens
CHUNK = 64


class Embedder:
    def __init__(self, cfg: Config, usage: Usage) -> None:
        self.usage = usage
        self._client = AsyncOpenAI(
            api_key=cfg.hackclub_api_key,
            base_url=cfg.llm_base_url,
            timeout=120.0,
            max_retries=0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts in batches, recording spend per batch."""
        out: list[list[float]] = []
        for i in range(0, len(texts), CHUNK):
            batch = [(t[:4000] if t and t.strip() else ".") for t in texts[i:i + CHUNK]]
            resp = await self._client.embeddings.create(model=EMBED_MODEL, input=batch)
            out.extend(d.embedding for d in resp.data)
            pt = resp.usage.prompt_tokens if resp.usage else 0
            cost = pt / 1_000_000 * EMBED_COST_PER_M_TOKENS
            await self.usage.record_embedding(EMBED_MODEL, pt, cost)
            log.info("embedded batch %d-%d (%d texts, $%.6f)", i, i + len(batch), len(batch), cost)
        return out

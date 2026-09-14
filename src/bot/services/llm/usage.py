"""Spend tracking and the daily budget ($3/day at hackai, resets 00:00 UTC)."""
from __future__ import annotations

import logging

from bot.config import Config
from bot.db.repo import Database
from bot.services.llm.catalog import ModelCatalog

log = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """Daily budget is nearly exhausted; paid calls are blocked."""


class Usage:
    def __init__(self, db: Database, cfg: Config, catalog: ModelCatalog) -> None:
        self.db = db
        self.cfg = cfg
        self.catalog = catalog

    async def spent_today(self) -> float:
        """Return today's total spend in USD."""
        return await self.db.spent_today()

    def remaining(self, spent: float) -> float:
        """Return the remaining budget for today."""
        return max(0.0, self.cfg.daily_budget_usd - spent)

    async def allows(self, model_id: str) -> bool:
        """Return True if a paid call to this model is allowed right now."""
        if self.catalog.is_free(model_id):
            return True
        spent = await self.spent_today()
        return spent < self.cfg.daily_budget_usd * self.cfg.budget_block

    async def record(
        self, model_id: str, task: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """Persist one call's token usage and return its cost in USD."""
        cost = self.catalog.cost_usd(model_id, prompt_tokens, completion_tokens)
        await self.db.add_usage(model_id, task, prompt_tokens, completion_tokens, cost)
        if cost > 0:
            spent = await self.spent_today()
            log.info(
                "spend: %s/%s -> $%.4f (today $%.2f of $%.2f)",
                model_id, task, cost, spent, self.cfg.daily_budget_usd,
            )
            warn_at = self.cfg.daily_budget_usd * self.cfg.budget_warn
            if spent >= warn_at:
                log.warning("Budget: $%.2f of $%.2f (warn %.0f%%)",
                            spent, self.cfg.daily_budget_usd, self.cfg.budget_warn * 100)
        return cost

    async def record_embedding(self, model_id: str, prompt_tokens: int, cost: float) -> None:
        """Persist an embeddings call (input tokens only)."""
        await self.db.add_usage(model_id, "embed", prompt_tokens, 0, cost)

    async def status_text(self) -> str:
        """Build the HTML spend summary shown in the admin panel."""
        spent = await self.spent_today()
        rows = await self.db.usage_today_breakdown()
        task_names = {"quick": "квитанции", "ocr": "распознавание",
                      "brain": "мозг", "writer": "писатель", "embed": "эмбеддинги"}
        lines = [
            f"💰 <b>Расход за сегодня (UTC)</b>",
            f"{spent:.4f} из {self.cfg.daily_budget_usd:.2f} $ "
            f"(осталось {self.remaining(spent):.2f} $)",
        ]
        if rows:
            lines.append("")
            for r in rows:
                name = task_names.get(r["task"], r["task"])
                lines.append(
                    f"• {name}: {r['calls']} выз., "
                    f"{r['pt'] or 0}+{r['ct'] or 0} ток., {r['cost']:.4f} $"
                )
        else:
            lines.append("Пока ни одного вызова.")
        lines.append("")
        lines.append("Сброс лимита hackai — в 00:00 UTC (03:00 МСК).")
        return "\n".join(lines)

"""Live smoke test of the hackai API and every role in the router.

Run from the repo's src/:  python scripts/smoke_test.py
Makes 5 cheap calls (total cost << $0.01) and prints a report:
1. Model catalog (GET /proxy/v1/models) and availability of routed ids
2. quick (free model) - receipt classification
3. brain - math
4. writer - human-like style
5. ocr - recognition of a generated text image
6. streaming - brain stream
7. Total: today's spend
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pymupdf as fitz  # noqa: E402
import httpx  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

from bot.config import Config  # noqa: E402
from bot.db.repo import Database  # noqa: E402
from bot.services.llm.catalog import ModelCatalog  # noqa: E402
from bot.services.llm.client import LLMClient  # noqa: E402
from bot.services.llm.prompts import build_receipt_messages  # noqa: E402
from bot.services.llm.router import FALLBACKS, Router, all_routed_ids  # noqa: E402
from bot.services.llm.usage import Usage  # noqa: E402
from bot.services.ocr import ocr_image  # noqa: E402

PASSED = "✅"
FAILED = "❌"


def make_test_image() -> bytes:
    """Render a PNG with task text (ASCII only: base fonts lack Cyrillic)."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((30, 60), "Algebra, grade 9", fontsize=14)
    page.insert_text((30, 90), "Exercise 214", fontsize=12)
    page.insert_text((30, 120), "Solve the equation: x^2 = 49", fontsize=11)
    pix = page.get_pixmap(dpi=110)
    data = pix.tobytes("png")
    doc.close()
    return data


async def main() -> int:
    """Run all checks and return a non-zero exit code on failures."""
    cfg = Config()
    if not cfg.hackclub_api_key:
        print(f"{FAILED} HACKCLUB_API_KEY is empty in .env")
        return 1
    cfg.ensure_dirs()

    client = AsyncOpenAI(
        api_key=cfg.hackclub_api_key,
        base_url=cfg.llm_base_url,
        timeout=httpx.Timeout(120.0, connect=15.0),
        max_retries=0,
    )
    print("\n=== 1. Model catalog ===")
    catalog = ModelCatalog(client)
    n = await catalog.refresh()
    if n == 0:
        print(f"{FAILED} empty catalog - check the key/network. Router falls back to config.")
    else:
        print(f"{PASSED} got {n} models from {cfg.llm_base_url}/models")

    ids = list(dict.fromkeys(all_routed_ids() + [m for v in FALLBACKS.values() for m in v]))
    missing: list[str] = []
    for model_id in ids:
        if not catalog.exists(model_id):
            missing.append(
                f"   {FAILED} {model_id}  -> closest: {catalog.closest(model_id)}"
            )
    print(f"Router ids checked: {len(ids)}")
    if missing:
        print("Missing from the catalog (fix router.py):")
        print("\n".join(missing))
    else:
        print(f"{PASSED} every id from router.py exists in the live catalog")

    db = Database(cfg.db_path)
    await db.connect()
    usage = Usage(db, cfg, catalog)
    llm = LLMClient(cfg, db, catalog, Router(catalog), usage)
    results: list[tuple[str, bool, str]] = []

    try:
        r = await llm.chat(
            "quick",
            build_receipt_messages("Упражнение 214. Решите уравнение x^2 = 49"),
        )
        results.append(("quick (receipt)", True, f"{r.model}: {r.text}"))
    except Exception as e:  # noqa: BLE001
        results.append(("quick (receipt)", False, str(e)[:200]))

    try:
        r = await llm.chat(
            "brain",
            [{"role": "user", "content": "Сколько будет 7*8? Ответь только числом."}],
            max_tokens=100,
        )
        ok = "56" in r.text
        results.append(("brain (math)", ok, f"{r.model}: {r.text.strip()[:80]}"))
    except Exception as e:  # noqa: BLE001
        results.append(("brain (math)", False, str(e)[:200]))

    try:
        r = await llm.chat(
            "writer",
            [
                {
                    "role": "system",
                    "content": "Ты пишешь как живой школьник, без ИИ-штампов, "
                    "предложения разной длины.",
                },
                {"role": "user", "content": "Два предложения про первый день осени в школе."},
            ],
            max_tokens=600,
        )
        results.append(
            ("writer (style)", bool(r.text.strip()), f"{r.model}: {r.text.strip()[:100]}…")
        )
    except Exception as e:  # noqa: BLE001
        results.append(("writer (style)", False, str(e)[:200]))

    try:
        text = await ocr_image(llm, make_test_image(), mime="image/png")
        ok = "214" in text
        results.append(("ocr (text image)", ok, f"recognized: {text[:100]!r}"))
    except Exception as e:  # noqa: BLE001
        results.append(("ocr (text image)", False, str(e)[:200]))

    try:
        chunks = 0
        async for _delta in llm.chat_stream(
            "brain",
            [{"role": "user", "content": "Посчитай вслух от 1 до 5, через запятую."}],
            max_tokens=300,
        ):
            chunks += 1
        results.append(("streaming (brain)", chunks > 1, f"{chunks} deltas"))
    except Exception as e:  # noqa: BLE001
        results.append(("streaming (brain)", False, str(e)[:200]))

    spent = await usage.spent_today()
    breakdown = await db.usage_today_breakdown()
    await db.close()

    print("\n=== 2. Live calls per role ===")
    failed = 0
    for name, ok, detail in results:
        if not ok:
            failed += 1
        print(f"{PASSED if ok else FAILED} {name}: {detail}")

    print("\n=== 3. Spend (stored in usage_log) ===")
    print(f"Today: ${spent:.4f} of ${cfg.daily_budget_usd:.2f}")
    for r in breakdown:
        print(f"  {r['task']}: {r['calls']} calls, {r['pt'] or 0}+{r['ct'] or 0} tokens, ${r['cost']:.4f}")

    verdict = "ALL OK" if failed == 0 and not missing else \
        f"problems: {failed} failed calls, {len(missing)} missing ids"
    print(f"\nTOTAL: {verdict}")
    return 0 if failed == 0 and not missing else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Смоук-тест живого API hackai + всех ролей роутинга.

Запуск из корня репо:  python scripts/smoke_test.py
Делает 5 дешёвых вызовов (общий расход << $0.01) и печатает отчёт:
1. Каталог моделей (GET /proxy/v1/models) и наличие всех ID из роутера
2. quick (бесплатная модель) — квантианция
3. brain — математика
4. writer — человечный стиль
5. ocr — распознавание сгенерированной картинки с текстом
6. streaming — стриминг мозга
7. Итог: расход за сегодня
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
    """PNG с текстом задания. Только ASCII: базовые шрифты fitz не умеют кириллицу."""
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
    cfg = Config()
    if not cfg.hackclub_api_key:
        print(f"{FAILED} HACKCLUB_API_KEY пуст в .env")
        return 1
    cfg.ensure_dirs()

    client = AsyncOpenAI(
        api_key=cfg.hackclub_api_key,
        base_url=cfg.llm_base_url,
        timeout=httpx.Timeout(120.0, connect=15.0),
        max_retries=0,
    )
    print("\n=== 1. Каталог моделей ===")
    catalog = ModelCatalog(client)
    n = await catalog.refresh()
    if n == 0:
        print(f"{FAILED} каталог пуст — проверь ключ/сеть. Роутер пойдёт по конфигу.")
    else:
        print(f"{PASSED} получил {n} моделей с {cfg.llm_base_url}/models")

    ids = list(dict.fromkeys(all_routed_ids() + [m for v in FALLBACKS.values() for m in v]))
    missing: list[str] = []
    for model_id in ids:
        if not catalog.exists(model_id):
            missing.append(
                f"   {FAILED} {model_id}  → похожие: {catalog.closest(model_id)}"
            )
    print(f"Проверено ID роутинга: {len(ids)}")
    if missing:
        print("Отсутствуют в каталоге (надо поправить router.py):")
        print("\n".join(missing))
    else:
        print(f"{PASSED} все ID из router.py есть в живом каталоге")

    db = Database(cfg.db_path)
    await db.connect()
    usage = Usage(db, cfg, catalog)
    llm = LLMClient(cfg, db, catalog, Router(catalog), usage)
    results: list[tuple[str, bool, str]] = []

    # --- quick: бесплатная модель, квантианция ------------------------------
    try:
        r = await llm.chat(
            "quick",
            build_receipt_messages("Упражнение 214. Решите уравнение x^2 = 49"),
        )
        results.append(("quick (квантианция)", True, f"{r.model}: {r.text}"))
    except Exception as e:  # noqa: BLE001
        results.append(("quick (квантианция)", False, str(e)[:200]))

    # --- brain: математика ---------------------------------------------------
    try:
        r = await llm.chat(
            "brain",
            [{"role": "user", "content": "Сколько будет 7*8? Ответь только числом."}],
            max_tokens=20,
        )
        ok = "56" in r.text
        results.append(("brain (математика)", ok, f"{r.model}: {r.text.strip()[:80]}"))
    except Exception as e:  # noqa: BLE001
        results.append(("brain (математика)", False, str(e)[:200]))

    # --- writer: человечный стиль ---------------------------------------------
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
            ("writer (стиль)", bool(r.text.strip()), f"{r.model}: {r.text.strip()[:100]}…")
        )
    except Exception as e:  # noqa: BLE001
        results.append(("writer (стиль)", False, str(e)[:200]))

    # --- ocr: картинка с текстом -----------------------------------------------
    try:
        text = await ocr_image(llm, make_test_image(), mime="image/png")
        ok = "214" in text
        results.append(("ocr (картинка с текстом)", ok, f"распознал: {text[:100]!r}"))
    except Exception as e:  # noqa: BLE001
        results.append(("ocr (картинка с текстом)", False, str(e)[:200]))

    # --- streaming --------------------------------------------------------------
    try:
        chunks = 0
        async for _delta in llm.chat_stream(
            "brain",
            [{"role": "user", "content": "Посчитай вслух от 1 до 5, через запятую."}],
            max_tokens=300,
        ):
            chunks += 1
        results.append(("streaming (мозг)", chunks > 1, f"{chunks} дельт"))
    except Exception as e:  # noqa: BLE001
        results.append(("streaming (мозг)", False, str(e)[:200]))

    spent = await usage.spent_today()
    breakdown = await db.usage_today_breakdown()
    await db.close()

    print("\n=== 2. Живые вызовы по ролям ===")
    failed = 0
    for name, ok, detail in results:
        if not ok:
            failed += 1
        print(f"{PASSED if ok else FAILED} {name}: {detail}")

    print("\n=== 3. Расход (записан в usage_log) ===")
    print(f"Сегодня: ${spent:.4f} из ${cfg.daily_budget_usd:.2f}")
    for r in breakdown:
        print(f"  {r['task']}: {r['calls']} выз., {r['pt'] or 0}+{r['ct'] or 0} ток., ${r['cost']:.4f}")

    verdict = "ВСЁ ОК 🎉" if failed == 0 and not missing else \
        f"есть проблемы: {failed} провальных вызовов, {len(missing)} отсутствующих ID"
    print(f"\nИТОГ: {verdict}")
    return 0 if failed == 0 and not missing else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

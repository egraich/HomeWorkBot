"""System and user prompts for all roles."""
from __future__ import annotations

import base64

from bot.db.repo import SessionInfo

KIND_LABELS = {
    "text": "текст",
    "forward": "пересланное сообщение",
    "photo": "фото (распознанный текст)",
}

QUICK_SYSTEM = (
    "Ты — служебный модуль ТГ-бота. Отвечай ОДНОЙ короткой строкой "
    "(3–8 слов), без точки в конце, без кавычек, без эмодзи."
)

OCR_SYSTEM = (
    "Ты — точный OCR-движок. Извлеки ВЕСЬ текст с изображения: печатный и "
    "рукописный, русский и английский.\n"
    "Правила:\n"
    "- Сохраняй структуру: заголовки, списки, нумерацию заданий.\n"
    "- Формулы записывай в LaTeX внутри $...$.\n"
    "- Если виден номер упражнения или страницы — вынеси его первой строкой: "
    "«Упражнение N» или «Страница N».\n"
    "- Нечитаемые места помечай [нечитаемо].\n"
    "- Никаких комментариев и пояснений — только распознанный текст."
)

PLANNER_SYSTEM = (
    "Ты — внимательный помощник школьника {class_name} по домашним заданиям. "
    "Ученик собрал домашку по предметам: {subjects}.\n\n"
    "Разбери всё, что он прислал, и составь план. Формат ответа строго такой:\n\n"
    "🤔 <b>Что я понял</b>\n"
    "2–5 предложений простым языком: что за задания прислали, где подвох, "
    "как будешь подходить, что стоит уточнить.\n\n"
    "📋 <b>План</b>\n"
    "1. [Предмет] Что за задание → как решать (1–2 строки) → сложность ⭐/⭐⭐/⭐⭐⭐\n"
    "…по каждой задаче из материалов.\n\n"
    "Последней строкой: «Напиши номер задачи — приступим. Можно задать вопрос "
    "или поменять план.»\n"
    "Пиши по-русски, живо и без воды. Если материалы противоречат учебнику — скажи."
)

BRAIN_SYSTEM = (
    "Ты — умный помощник школьника {class_name}. Помогаешь с домашкой по "
    "предметам: {subjects}.\n"
    "Ситуация: план домашки уже составлен, теперь ученик командует задачами "
    "по номеру и задаёт вопросы.\n"
    "Правила:\n"
    "- Решайте пошагово, но компактно: ключевые шаги без воды.\n"
    "- Формулы — в LaTeX внутри $...$.\n"
    "- Если задание из учебника, опирайся на приложенные фрагменты страниц; "
    "не выдумывай номера упражнений.\n"
    "- Отвечай по-русски. Не используй markdown-заголовки ## — только абзацы, "
    "списки и **выделение**."
)

WRITER_DRAFT_SYSTEM = (
    "Ты готовишь материал для школьного сочинения. Составь содержательный "
    "каркас: тезис, аргументы, конкретные примеры (из произведения или жизни), "
    "вывод. Это рабочие заметки для второго этапа, списки допустимы. Пиши по-русски."
)

WRITER_FINAL_SYSTEM = (
    "Ты пишешь школьные сочинения так, что текст выглядит написанным живым "
    "школьником, а не ИИ.\n\n"
    "ЗАПРЕЩЕНО (типичные ИИ-штампы): «в современном мире», «в наше время», "
    "«на сегодняшний день», «не секрет, что», «стоит отметить», «важно "
    "подчеркнуть», «играет важную роль», «динамично развивается», «таким "
    "образом», «подводя итог», «можно сделать вывод», канцелярит, цепочки "
    "причастных оборотов.\n\n"
    "КАК ПИСАТЬ:\n"
    "- Предложения РАЗНОЙ длины: рядом короткое (2–4 слова) и длинное (15–25 слов).\n"
    "- Никаких списков, заголовков и маркеров — сплошная проза, абзацы.\n"
    "- Конкретика вместо общих слов: имена, детали, события.\n"
    "- Лёгкая разговорность («по-моему», «мне кажется»), без чат-сленга.\n"
    "- Мысль может чуть петлять, но текст понятный и логичный.\n"
    "- Никаких упоминаний ИИ и ботов.\n"
    "{style_rule}\n\n"
    "Пиши по-русски. Выдай ТОЛЬКО готовый текст сочинения — без вступлений, "
    "пояснений и кавычек вокруг текста."
)

STYLE_RULES = {
    "clean": "- Стиль: грамотный школьник, пишет чисто, орфография безупречна.",
    "imperfect": (
        "- Стиль: обычный школьник: простая лексика, местами разговорные "
        "обороты, лёгкие шероховатости построения фраз — но орфографию соблюдает."
    ),
}


def _image_part(image_bytes: bytes) -> dict:
    """Build an image content part from raw JPEG bytes."""
    return {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()},
    }


def build_ocr_messages(image_data_url: str) -> list[dict]:
    """Build the OCR request messages with an image content part."""
    return [
        {"role": "system", "content": OCR_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        },
    ]


def build_receipt_messages(ocr_text: str) -> list[dict]:
    """Build the short receipt request describing a recognized photo."""
    return [
        {"role": "system", "content": QUICK_SYSTEM},
        {
            "role": "user",
            "content": (
                "Фото задания от школьника. Вот распознанный текст:\n"
                f"{ocr_text[:1500]}\n\n"
                "В 3–8 словах скажи, что это (предмет, номер упражнения/тема)."
            ),
        },
    ]


def build_plan_messages(
    class_name: str,
    subject_names: list[str],
    items: list[dict],
    excerpts: list[dict],
    page_images: list[tuple[int, bytes]] | None = None,
) -> list[dict]:
    """Build the planner request from collected materials and textbook excerpts."""
    parts = [f"Класс: {class_name}.", f"Предметы на завтра: {', '.join(subject_names)}.", ""]
    parts.append("Материалы от ученика:")
    for i, item in enumerate(items, 1):
        label = KIND_LABELS.get(item["kind"], "текст")
        text = item["content"] if item["kind"] != "photo" else item["content"][:2000]
        parts.append(f"\n{i}. [{label}]\n{text}")
    if excerpts:
        parts.append("\nРелевантные страницы учебников (текстовый слой может быть "
                     "искажён для формул — ниже приложены изображения страниц):")
        for e in excerpts:
            parts.append(f"--- стр. {e['page_no']} ---\n{e['text'][:1200]}")
    system = {
        "role": "system",
        "content": PLANNER_SYSTEM.format(
            class_name=class_name, subjects=", ".join(subject_names)
        ),
    }
    user_text = "\n".join(parts)
    if not page_images:
        return [system, {"role": "user", "content": user_text}]
    content: list[dict] = [{"type": "text", "text": user_text}]
    for page_no, image in page_images:
        content.append({"type": "text", "text": f"\n\nСтраница {page_no} учебника:"})
        content.append(_image_part(image))
    return [system, {"role": "user", "content": content}]


def build_dialog_messages(
    class_name: str,
    subject_names: list[str],
    history: list[dict],
    excerpts: list[dict],
    extra_image: bytes | None = None,
) -> list[dict]:
    """Build the dialog request from trimmed history and textbook excerpts."""
    messages: list[dict] = [
        {
            "role": "system",
            "content": BRAIN_SYSTEM.format(
                class_name=class_name, subjects=", ".join(subject_names)
            ),
        }
    ]
    for h in history:
        content = h["content"]
        if h["kind"] == "photo":
            content = f"[фото задания, распознанный текст]\n{content}"
        messages.append({"role": h["role"], "content": content})
    if excerpts:
        ctx = "\n\n".join(
            f"Страница {e['page_no']} учебника:\n{e['text'][:1200]}" for e in excerpts
        )
        messages.append({"role": "system", "content": f"Релевантные фрагменты учебника:\n{ctx}"})
    if extra_image:
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Фото задания от ученика — решай по нему "
                        "(распознанный текст выше может быть неточным для формул):",
                    },
                    _image_part(extra_image),
                ],
            }
        )
    return messages


def build_writer_draft_messages(topic: str, class_name: str, requirements: str) -> list[dict]:
    """Build the essay outline request for the brain model."""
    return [
        {"role": "system", "content": WRITER_DRAFT_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Ученик {class_name}. Тема: {topic}\n"
                f"Требования/контекст: {requirements or '—'}\n\n"
                "Составь рабочий каркас сочинения."
            ),
        },
    ]


def build_writer_final_messages(
    topic: str, class_name: str, words: int, draft: str, style: str
) -> list[dict]:
    """Build the final human-style essay request for the writer model."""
    style_rule = STYLE_RULES.get(style, STYLE_RULES["clean"])
    return [
        {"role": "system", "content": WRITER_FINAL_SYSTEM.format(style_rule=style_rule)},
        {
            "role": "user",
            "content": (
                f"Тема: {topic}\nКласс: {class_name}\nОбъём: примерно {words} слов.\n\n"
                "Рабочий каркас (используй мысли, но не копируй формулировки):\n"
                f"{draft}"
            ),
        },
    ]

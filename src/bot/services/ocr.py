"""OCR изображений через vision-модель (task=ocr в роутере)."""
from __future__ import annotations

import base64

from bot.services.llm.client import LLMClient
from bot.services.llm.prompts import build_ocr_messages


async def ocr_image(client: LLMClient, image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Фото (рука/доска/учебник) → текст. Формулы — LaTeX."""
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    result = await client.chat(
        "ocr", build_ocr_messages(data_url), temperature=0.0
    )
    return result.text.strip()

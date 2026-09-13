"""Image OCR via the vision model (task=ocr in the router)."""
from __future__ import annotations

import base64

from bot.services.llm.client import LLMClient
from bot.services.llm.prompts import build_ocr_messages


async def ocr_image(client: LLMClient, image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Extract text from a photo (handwriting, board, textbook) as LaTeX-marked text."""
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    result = await client.chat(
        "ocr", build_ocr_messages(data_url), temperature=0.0
    )
    return result.text.strip()

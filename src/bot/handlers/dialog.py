"""Dialog mode: task execution after the plan, essays, questions, photos."""
from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import dialog_kb
from bot.services.container import Services
from bot.services.llm.client import BudgetExceeded, LLMError
from bot.services.llm.prompts import (
    build_dialog_messages,
    build_writer_draft_messages,
    build_writer_final_messages,
)
from bot.services.ocr import ocr_image
from bot.services.planner import textbook_excerpts, trim_history
from bot.states import SessionFSM
from bot.utils.tg_helpers import stream_to_telegram

router = Router(name="dialog")
log = logging.getLogger(__name__)

ESSAY_RE = re.compile(r"\b(сочинени\w*|эссе|изложени\w*|доклад\w*|рассуждени\w*)\b", re.I)
WORDS_RE = re.compile(r"(\d{2,4})\s*(?:слов|знак)", re.I)
DEFAULT_WORDS = 300


async def _names(services: Services, session) -> tuple[str, list[str]]:
    """Return the class name and subject names of a session."""
    from bot.handlers.session import _class_name, _subject_rows

    class_name = await _class_name(services, session)
    subject_names = [s["name"] for s in await _subject_rows(services, session)]
    return class_name, subject_names


async def _run_brain(
    message: Message, services: Services, bot: Bot, session, class_name: str,
    subject_names: list[str], text: str, image_bytes: bytes | None = None,
) -> None:
    """Answer a dialog message with the brain model, streaming to Telegram."""
    history = await services.db.list_messages(session.id, limit=24)
    excerpts = await textbook_excerpts(services.db, session, [text])

    # vision-capable brains get the actual page images: formula text layers
    # of textbooks are often too mangled to read
    page_images: list[tuple[int, bytes]] = []
    if excerpts and services.router.primary_vision(
        await services.llm.current_mode(), "brain"
    ):
        for e in excerpts[:2]:
            row = await services.db.get_textbook(e["textbook_id"])
            if row:
                image = await services.textbooks.render_page(row["filename"], e["page_no"])
                if image:
                    page_images.append((e["page_no"], image))

    llm_messages = build_dialog_messages(
        class_name, subject_names, trim_history(history), excerpts,
        extra_image=image_bytes, page_images=page_images or None,
    )
    answer = await stream_to_telegram(
        bot, message.chat.id, services.llm.chat_stream("brain", llm_messages),
        placeholder="🤔 Думаю…",
    )
    await services.db.add_message(session.id, "assistant", "text", answer)


async def _run_essay(
    message: Message, services: Services, bot: Bot, session, class_name: str,
    text: str,
) -> None:
    """Write a school essay via the draft-then-humanize pipeline, streaming it."""
    words_match = WORDS_RE.search(text)
    words = int(words_match.group(1)) if words_match else DEFAULT_WORDS
    draft = await services.llm.chat(
        "brain", build_writer_draft_messages(text, class_name, ""), max_tokens=900
    )
    answer = await stream_to_telegram(
        bot, message.chat.id,
        services.llm.chat_stream(
            "writer",
            build_writer_final_messages(text, class_name, words, draft.text, session.essay_style),
        ),
        placeholder="✍️ Пишу как живой…",
    )
    await services.db.add_message(session.id, "assistant", "text", answer)


@router.message(StateFilter(SessionFSM.dialog), F.photo)
async def dialog_photo(
    message: Message, state: FSMContext, services: Services, bot: Bot
) -> None:
    """OCR a photo sent in dialog and hand it to the brain (vision when possible)."""
    data = await state.get_data()
    if data.get("busy"):
        await message.answer("⏳ Секунду, доделываю предыдущее…")
        return
    session = await services.db.get_session(data.get("session_id", 0))
    if not session or session.status != "dialog":
        await state.clear()
        await message.answer("Сессия закончилась — начни заново через меню.")
        return

    status = await message.answer("📸 Распознаю…")
    try:
        buf = await bot.download(message.photo[-1])
    except Exception as e:  # noqa: BLE001
        await status.edit_text(f"❌ Не смог скачать фото: <code>{e}</code>")
        return
    if buf is None:
        await status.edit_text("❌ Не смог скачать фото")
        return
    image_bytes = buf.getvalue()

    ocr_text = ""
    try:
        ocr_text = await ocr_image(services.llm, image_bytes)
    except (BudgetExceeded, LLMError):
        pass
    await services.db.add_message(
        session.id, "user", "photo", ocr_text or "[фото без текста]",
        {"file_id": message.photo[-1].file_id},
    )

    mode = await services.llm.current_mode()
    vision = services.router.primary_vision(mode, "brain")
    await status.edit_text("📸 Принял фото" if vision else "📸 Принял фото, разбираю текст")
    await state.update_data(busy=True)
    try:
        class_name, subject_names = await _names(services, session)
        await _run_brain(
            message, services, bot, session, class_name, subject_names,
            ocr_text or "задание с фото",
            image_bytes=image_bytes if vision else None,
        )
    except BudgetExceeded as e:
        await message.answer(f"💸 {e}")
    except LLMError as e:
        await message.answer(f"❌ Модели не ответили ({e}). Попробуй ещё раз.")
    finally:
        await state.update_data(busy=False)


@router.message(StateFilter(SessionFSM.dialog), F.text, ~F.text.startswith("/"))
async def dialog_message(
    message: Message, state: FSMContext, services: Services, bot: Bot
) -> None:
    """Route a dialog message to the essay pipeline or the brain chat."""
    data = await state.get_data()
    if data.get("busy"):
        await message.answer("⏳ Секунду, доделываю предыдущее…")
        return
    session = await services.db.get_session(data.get("session_id", 0))
    if not session or session.status != "dialog":
        await state.clear()
        await message.answer("Сессия закончилась — начни заново через меню.")
        return

    class_name, subject_names = await _names(services, session)
    await services.db.add_message(session.id, "user", "text", message.text or "")
    await state.update_data(busy=True)
    try:
        if ESSAY_RE.search(message.text or ""):
            await _run_essay(message, services, bot, session, class_name, message.text)
        else:
            await _run_brain(
                message, services, bot, session, class_name, subject_names, message.text
            )
    except BudgetExceeded as e:
        await message.answer(f"💸 {e}")
    except LLMError as e:
        await message.answer(f"❌ Модели не ответили ({e}). Попробуй ещё раз.")
    finally:
        await state.update_data(busy=False)


@router.callback_query(StateFilter(SessionFSM.dialog), F.data == "sess:style")
async def toggle_style(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    """Toggle the essay style between clean and imperfect."""
    data = await state.get_data()
    session = await services.db.get_session(data.get("session_id", 0))
    if not session:
        await callback.answer("Нет активной сессии", show_alert=True)
        return
    new_style = "imperfect" if session.essay_style == "clean" else "clean"
    await services.db.set_session_essay_style(session.id, new_style)
    await callback.answer(
        "Стиль: живой, с шероховатостями" if new_style == "imperfect" else "Стиль: чистый"
    )
    await callback.message.edit_reply_markup(reply_markup=dialog_kb(new_style))


@router.message(StateFilter(SessionFSM.dialog), Command("стиль", "style"))
async def cmd_style(
    message: Message, state: FSMContext, services: Services
) -> None:
    """Toggle the essay style between clean and imperfect via command."""
    data = await state.get_data()
    session = await services.db.get_session(data.get("session_id", 0))
    if not session:
        await message.answer("Нет активной сессии.")
        return
    new_style = "imperfect" if session.essay_style == "clean" else "clean"
    await services.db.set_session_essay_style(session.id, new_style)
    await message.answer(
        "✒️ Стиль сочинений: <b>живой, с шероховатостями</b>"
        if new_style == "imperfect"
        else "✒️ Стиль сочинений: <b>чистый</b>"
    )


@router.message(StateFilter(SessionFSM.dialog), Command("план", "plan"))
async def cmd_plan_in_dialog(message: Message) -> None:
    """Explain that the plan already exists when /plan is typed in dialog."""
    await message.answer(
        "📋 План уже составлен — он выше. Командуй задачей по номеру, "
        "или /сброс чтобы собрать новую домашку."
    )

"""Сессия домашки: выбор класса/предметов, режим слушания, составление плана."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db.repo import SessionInfo
from bot.keyboards import classes_kb, collecting_kb, dialog_kb, subjects_kb
from bot.services.container import Services
from bot.services.llm.client import BudgetExceeded, LLMError
from bot.services.llm.prompts import build_plan_messages, build_receipt_messages
from bot.services.ocr import ocr_image
from bot.services.planner import collect_session_items, textbook_excerpts
from bot.states import SessionFSM
from bot.utils.tg_helpers import stream_to_telegram

router = Router(name="session")
log = logging.getLogger(__name__)

COLLECTING_WELCOME = (
    "🎧 <b>Слушаю</b>\n\n"
    "Накидывай всё по домашке: текст, пересылки сообщений, фото упражнений и "
    "доски. Если задание из учебника — лучше просто напиши номер, я найду "
    "страницы сам.\n\n"
    "Когда всё скинул — жми <b>«Составить план»</b> или /план."
)


async def _subject_rows(services: Services, session: SessionInfo) -> list[dict]:
    rows = []
    for sid in session.subject_ids:
        s = await services.db.get_subject(sid)
        if s:
            rows.append(dict(s))
    return rows


async def _class_name(services: Services, session: SessionInfo) -> str:
    if session.class_id:
        c = await services.db.get_class(session.class_id)
        if c:
            return c["name"]
    return "школьника"


# --- старт домашки -------------------------------------------------------


async def _start_subjects(
    callback: CallbackQuery, state: FSMContext, services: Services, class_id: int
) -> None:
    subjects = await services.db.list_subjects(class_id)
    if not subjects:
        await callback.answer(
            "В классе ещё нет предметов — админ добавит через /admin", show_alert=True
        )
        return
    await state.set_state(SessionFSM.choosing_subjects)
    await state.update_data(selected=[])
    await callback.message.edit_text(
        "Выбери предметы, по которым есть домашка на завтра:",
        reply_markup=subjects_kb(subjects, set()),
    )


@router.callback_query(F.data == "hw:start")
async def hw_start(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    await callback.answer()
    class_id = await services.db.get_user_class(callback.from_user.id)
    if class_id is None:
        classes = await services.db.list_classes()
        if not classes:
            await callback.answer("Классы ещё не заведены", show_alert=True)
            return
        await state.set_state(SessionFSM.choosing_class)
        await state.update_data(after_class="subjects")
        await callback.message.edit_text(
            "Выбери класс:", reply_markup=classes_kb(classes)
        )
        return
    await _start_subjects(callback, state, services, class_id)


@router.callback_query(SessionFSM.choosing_class, F.data.startswith("cls:"))
async def choose_class(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    class_id = int(callback.data.split(":")[1])
    await services.db.set_user_class(callback.from_user.id, class_id)
    await callback.answer()
    data = await state.get_data()
    if data.get("after_class") == "subjects":
        await _start_subjects(callback, state, services, class_id)
    else:
        await state.clear()
        from bot.handlers.common import show_menu

        await show_menu(callback, services, callback.from_user.id)


@router.callback_query(SessionFSM.choosing_subjects, F.data.startswith("subj:"))
async def toggle_subject(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    sid = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected: set[int] = set(data.get("selected", []))
    if sid in selected:
        selected.discard(sid)
    else:
        selected.add(sid)
    await state.update_data(selected=list(selected))
    await callback.answer()
    class_id = await services.db.get_user_class(callback.from_user.id)
    subjects = await services.db.list_subjects(class_id)
    await callback.message.edit_reply_markup(
        reply_markup=subjects_kb(subjects, selected)
    )


@router.callback_query(SessionFSM.choosing_subjects, F.data == "subj:done")
async def subjects_done(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    data = await state.get_data()
    selected = data.get("selected", [])
    if not selected:
        await callback.answer("Выбери хотя бы один предмет", show_alert=True)
        return
    class_id = await services.db.get_user_class(callback.from_user.id)
    session_id = await services.db.create_session(
        callback.from_user.id, callback.message.chat.id, class_id, selected
    )
    await state.set_state(SessionFSM.collecting)
    await state.update_data(session_id=session_id)
    await callback.answer()
    await callback.message.edit_text(COLLECTING_WELCOME)


# --- режим слушания -------------------------------------------------------


async def _accept(
    message: Message, state: FSMContext, services: Services, kind: str,
    content: str, meta: dict | None = None, emoji: str = "✅",
) -> None:
    data = await state.get_data()
    session_id = data["session_id"]
    await services.db.add_message(session_id, "user", kind, content, meta)
    n = await services.db.count_messages(session_id)
    await message.answer(f"{emoji} Принял ({n})", reply_markup=collecting_kb())


@router.message(StateFilter(SessionFSM.collecting), F.text, ~F.text.startswith("/"))
async def collect_text(
    message: Message, state: FSMContext, services: Services
) -> None:
    kind = "forward" if message.forward_origin else "text"
    meta = {}
    if message.forward_origin is not None:
        origin = message.forward_origin
        name = getattr(origin, "sender_user_name", None)
        sender = getattr(origin, "sender_user", None)
        if not name and sender is not None:
            name = sender.full_name
        meta["forward_from"] = name or "неизвестно"
    await _accept(message, state, services, kind, message.text or "", meta or None)


@router.message(StateFilter(SessionFSM.collecting), F.photo)
async def collect_photo(
    message: Message, state: FSMContext, services: Services, bot: Bot
) -> None:
    status = await message.answer("📸 Распознаю…")
    photo = message.photo[-1]
    buf = await bot.download(photo)
    if buf is None:
        await status.edit_text("❌ Не смог скачать фото")
        return
    try:
        ocr_text = await ocr_image(services.llm, buf.getvalue())
    except (BudgetExceeded, LLMError) as e:
        await status.edit_text(f"❌ OCR не сработал: {e}")
        return
    if not ocr_text:
        await status.edit_text(
            "❌ Ничего не разобрал. Попробуй фото получше или напиши номер задания текстом."
        )
        return
    data = await state.get_data()
    await services.db.add_message(
        data["session_id"], "user", "photo", ocr_text, {"file_id": photo.file_id}
    )
    try:
        receipt = await services.llm.chat("quick", build_receipt_messages(ocr_text))
        short = receipt.text
    except (BudgetExceeded, LLMError):
        short = "задание"
    await status.edit_text(f"📸 Принял: {short}", reply_markup=collecting_kb())


@router.message(StateFilter(SessionFSM.collecting), F.document)
async def collect_document(
    message: Message, state: FSMContext, services: Services, bot: Bot
) -> None:
    doc = message.document
    mime = doc.mime_type or ""
    if mime.startswith("image/"):
        status = await message.answer("📸 Распознаю…")
        buf = await bot.download(doc)
        if buf is None:
            await status.edit_text("❌ Не смог скачать файл")
            return
        try:
            ocr_text = await ocr_image(services.llm, buf.getvalue())
        except (BudgetExceeded, LLMError) as e:
            await status.edit_text(f"❌ OCR не сработал: {e}")
            return
        data = await state.get_data()
        await services.db.add_message(
            data["session_id"], "user", "photo", ocr_text, {"file_id": doc.file_id}
        )
        await status.edit_text("📸 Принял картинку", reply_markup=collecting_kb())
        return
    if mime.startswith("text/") and (doc.file_size or 0) < 200_000:
        buf = await bot.download(doc)
        if buf:
            await _accept(
                message, state, services, "text", buf.getvalue().decode("utf-8", "replace")
            )
            return
    await message.answer(
        "📄 PDF в сессию не принимаю — книги грузятся через /admin → «Книги» "
        "(или по SSH в data/textbooks). Сюда: текст и фото."
    )


# --- план -------------------------------------------------------------------


async def _make_plan(
    message_or_cb: Message | CallbackQuery, state: FSMContext,
    services: Services, bot: Bot,
) -> None:
    chat_id = message_or_cb.chat.id if isinstance(message_or_cb, Message) \
        else message_or_cb.message.chat.id
    data = await state.get_data()
    session = await services.db.get_session(data["session_id"])
    if not session:
        await state.clear()
        return
    items = await collect_session_items(services.db, session)
    if not items:
        if isinstance(message_or_cb, CallbackQuery):
            await message_or_cb.answer("Сначала накидай задания", show_alert=True)
        else:
            await message_or_cb.answer("Сначала накидай задания — я пока пустой.")
        return

    subject_rows = await _subject_rows(services, session)
    subject_names = [s["name"] for s in subject_rows]
    class_name = await _class_name(services, session)
    excerpts = await textbook_excerpts(
        services.db, session, [i["content"] for i in items]
    )
    llm_messages = build_plan_messages(class_name, subject_names, items, excerpts)

    await state.set_state(SessionFSM.dialog)
    await state.update_data(busy=True, session_id=session.id)
    try:
        plan_text = await stream_to_telegram(
            bot, chat_id, services.llm.chat_stream("brain", llm_messages),
            placeholder="🤔 Разбираю домашку…",
        )
        await services.db.add_message(session.id, "assistant", "plan", plan_text)
        await bot.send_message(
            chat_id,
            "💬 Теперь просто командуй: «реши 1», «сочинение на тему …», "
            "или задай вопрос по любой задаче.",
            reply_markup=dialog_kb(session.essay_style),
        )
    except BudgetExceeded as e:
        await bot.send_message(chat_id, f"💸 {e}")
        await state.set_state(SessionFSM.collecting)
    except LLMError as e:
        await bot.send_message(chat_id, f"❌ Модели не ответили: {e}")
        await state.set_state(SessionFSM.collecting)
    finally:
        await state.update_data(busy=False)


@router.callback_query(StateFilter(SessionFSM.collecting), F.data == "sess:plan")
async def cb_plan(
    callback: CallbackQuery, state: FSMContext, services: Services, bot: Bot
) -> None:
    await callback.answer()
    await _make_plan(callback, state, services, bot)


@router.message(StateFilter(SessionFSM.collecting), Command("план", "plan"))
async def cmd_plan(
    message: Message, state: FSMContext, services: Services, bot: Bot
) -> None:
    await _make_plan(message, state, services, bot)


# --- управление сессией -------------------------------------------------------


async def _end_session(
    message: Message, state: FSMContext, services: Services, status: str, note: str
) -> None:
    await state.clear()
    active = await services.db.get_active_session(message.from_user.id)
    if active:
        await services.db.set_session_status(active.id, status)
    from bot.handlers.common import show_menu

    await message.answer(note)
    await show_menu(message, services, message.from_user.id)


@router.message(Command("стоп", "stop"))
async def cmd_stop(message: Message, state: FSMContext, services: Services) -> None:
    await _end_session(message, state, services, "done", "⏹ Сессия завершена. Удачи с домашкой!")


@router.message(Command("сброс", "reset"))
async def cmd_reset(message: Message, state: FSMContext, services: Services) -> None:
    await _end_session(message, state, services, "cancelled", "♻️ Сессия сброшена, накопленное забыто.")


@router.callback_query(F.data == "sess:stop")
async def cb_stop(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    await callback.answer()
    await state.clear()
    active = await services.db.get_active_session(callback.from_user.id)
    if active:
        await services.db.set_session_status(active.id, "done")
    from bot.handlers.common import show_menu

    await callback.message.edit_text("⏹ Сессия завершена.")
    await show_menu(callback, services, callback.from_user.id)


@router.callback_query(F.data == "sess:reset")
async def cb_reset(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    await callback.answer()
    await state.clear()
    active = await services.db.get_active_session(callback.from_user.id)
    if active:
        await services.db.set_session_status(active.id, "cancelled")
    from bot.handlers.common import show_menu

    await callback.message.edit_text("♻️ Сброшено.")
    await show_menu(callback, services, callback.from_user.id)


@router.callback_query(F.data == "sess:resume")
async def cb_resume(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    session = await services.db.get_active_session(callback.from_user.id)
    if not session:
        await callback.answer("Нет активной сессии", show_alert=True)
        return
    await callback.answer()
    await state.update_data(session_id=session.id, busy=False)
    if session.status == "collecting":
        await state.set_state(SessionFSM.collecting)
        await callback.message.edit_text(COLLECTING_WELCOME)
    else:
        await state.set_state(SessionFSM.dialog)
        await callback.message.edit_text(
            "💬 Продолжаем диалог. Командуй задачей или задай вопрос.",
            reply_markup=dialog_kb(session.essay_style),
        )

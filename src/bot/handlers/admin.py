"""Админка: режим качества, расходы, книги, классы и предметы."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import QUALITY_MODES
from bot.keyboards import (
    MODE_TITLES,
    admin_back_kb,
    admin_menu,
    bind_subject_kb,
    books_kb,
    classes_admin_kb,
    modes_kb,
    subjects_admin_kb,
)
from bot.services.container import Services
from bot.services.textbooks import unregistered_files
from bot.states import AdminFSM

router = Router(name="admin")
log = logging.getLogger(__name__)


def _is_admin(services: Services, tg_id: int) -> bool:
    return services.cfg.is_admin(tg_id)


async def _show_admin_menu(target: Message | CallbackQuery, services: Services) -> None:
    mode = await services.llm.current_mode()
    text = (
        "🛠 <b>Админка</b>\n"
        f"Режим качества: <b>{MODE_TITLES[mode]}</b>\n"
        "Книги кладутся по SSH в <code>data/textbooks/</code>."
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=admin_menu())
    else:
        await target.answer(text, reply_markup=admin_menu())


@router.message(Command("admin", "админ"))
async def cmd_admin(
    message: Message, state: FSMContext, services: Services
) -> None:
    if not _is_admin(services, message.from_user.id):
        await message.answer("🔒 Только для админов.")
        return
    await state.clear()
    await _show_admin_menu(message, services)


@router.callback_query(F.data == "adm")
async def cb_admin(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    if not _is_admin(services, callback.from_user.id):
        await callback.answer("🔒 Только для админов", show_alert=True)
        return
    await callback.answer()
    await _show_admin_menu(callback, services)


# --- режим качества -------------------------------------------------------

@router.callback_query(F.data == "adm:mode")
async def adm_mode(
    callback: CallbackQuery, services: Services
) -> None:
    if not _is_admin(services, callback.from_user.id):
        return
    await callback.answer()
    mode = await services.llm.current_mode()
    await callback.message.edit_text(
        "🎚 <b>Режим качества</b>\n"
        "Меняет набор моделей для всех задач сразу. Эконом бережёт дневной бюджет.",
        reply_markup=modes_kb(mode),
    )


@router.callback_query(F.data.startswith("adm:mode:"))
async def adm_mode_set(
    callback: CallbackQuery, services: Services
) -> None:
    if not _is_admin(services, callback.from_user.id):
        return
    mode = callback.data.split(":")[2]
    if mode not in QUALITY_MODES:
        await callback.answer("Неизвестный режим")
        return
    await services.db.set_setting("quality_mode", mode)
    await callback.answer(f"Режим: {MODE_TITLES[mode]}")
    await callback.message.edit_reply_markup(reply_markup=modes_kb(mode))


@router.callback_query(F.data == "adm:spend")
async def adm_spend(
    callback: CallbackQuery, services: Services
) -> None:
    if not _is_admin(services, callback.from_user.id):
        return
    await callback.answer()
    text = await services.usage.status_text()
    await callback.message.edit_text(text, reply_markup=admin_back_kb())


# --- книги ------------------------------------------------------------------

@router.callback_query(F.data == "adm:books")
async def adm_books(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    if not _is_admin(services, callback.from_user.id):
        return
    await callback.answer()
    registered = await services.db.list_textbooks()
    known = {r["filename"] for r in registered}
    files = unregistered_files(services.cfg, known)
    await state.update_data(pending_files=[str(p) for p in files])
    lines = ["📖 <b>Книги</b>"]
    if files:
        lines.append(f"\nНезарегистрированных PDF: {len(files)} — выбери и привяжи к предмету:")
    else:
        lines.append("\nНезарегистрированных PDF нет. Кинь файл в data/textbooks и вернись сюда.")
    if registered:
        lines.append("\nУже в базе:")
        for r in registered[:10]:
            scan = " (скан)" if r["is_scanned"] else ""
            lines.append(
                f"• {r['filename']} → {r['class_name']}/{r['subject_name']}, "
                f"{r['pages']} стр.{scan}"
            )
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=books_kb(files, len(registered))
    )


@router.callback_query(F.data.startswith("adm:book:"))
async def adm_book_pick(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    if not _is_admin(services, callback.from_user.id):
        return
    idx = callback.data.split(":")[2]
    if idx == "list":
        await callback.answer()
        registered = await services.db.list_textbooks()
        lines = ["📚 <b>Зарегистрированные книги</b>"] if registered else ["Пока пусто."]
        for r in registered:
            lines.append(
                f"• {r['filename']} → {r['class_name']}/{r['subject_name']}, {r['pages']} стр."
            )
        await callback.message.edit_text("\n".join(lines), reply_markup=admin_back_kb())
        return
    data = await state.get_data()
    files = data.get("pending_files", [])
    try:
        path = files[int(idx)]
    except (ValueError, IndexError):
        await callback.answer("Список устарел, открой книги заново", show_alert=True)
        return
    await state.set_state(AdminFSM.binding_book)
    await state.update_data(pending_path=path)
    await callback.answer()
    subjects = await services.db.list_all_subjects()
    if not subjects:
        await state.clear()
        await callback.message.edit_text(
            "Сначала создай хотя бы один предмет.", reply_markup=admin_back_kb()
        )
        return
    await callback.message.edit_text(
        f"Книга: <b>{Path(path).name}</b>\nК какому предмету привязать?",
        reply_markup=bind_subject_kb(subjects),
    )


@router.callback_query(AdminFSM.binding_book, F.data.startswith("adm:bind:"))
async def adm_bind_book(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    subject_id = int(callback.data.split(":")[2])
    data = await state.get_data()
    path = Path(data.get("pending_path", ""))
    await state.clear()
    subject = await services.db.get_subject(subject_id)
    if not subject or not path.exists():
        await callback.answer("Что-то потерялось, попробуй заново", show_alert=True)
        return
    await callback.answer("Запустил инжест")
    textbook_id = await services.db.add_textbook(
        subject_id, path.name, path.stem
    )
    progress = await callback.message.edit_text(
        f"⏳ Инжест <b>{path.name}</b> → {subject['name']}…"
    )

    async def progress_cb(done: int, total_ocr: int, pages: int) -> None:
        try:
            await progress.edit_text(
                f"⏳ {path.name}: страниц {pages}, OCR {done}/{total_ocr}"
            )
        except Exception:  # noqa: BLE001 — прогресс не критичен
            pass

    async def runner() -> None:
        try:
            stats = await services.textbooks.ingest(
                path, subject_id, textbook_id, progress_cb=progress_cb
            )
            scan = " (скан)" if stats["is_scanned"] else ""
            await progress.edit_text(
                f"✅ <b>{path.name}</b>: {stats['pages']} стр., "
                f"OCR: {stats['ocr_pages']}{scan}\n"
                f"Предмет: {subject['name']}"
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Инжест упал")
            await services.db.fail_textbook(textbook_id)
            await progress.edit_text(f"❌ Инжест упал: <code>{e}</code>")

    asyncio.create_task(runner())


# --- классы и предметы --------------------------------------------------------

@router.callback_query(F.data == "adm:classes")
async def adm_classes(
    callback: CallbackQuery, services: Services
) -> None:
    if not _is_admin(services, callback.from_user.id):
        return
    await callback.answer()
    classes = await services.db.list_classes()
    await callback.message.edit_text(
        "🏫 <b>Классы и предметы</b>\nНажми на класс, чтобы увидеть предметы "
        "(кнопка с ➖ удаляет предмет).",
        reply_markup=classes_admin_kb(classes),
    )


@router.callback_query(F.data == "adm:cls:add")
async def adm_class_add(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await callback.answer()
    await state.set_state(AdminFSM.adding_class)
    await callback.message.edit_text(
        "Пришли название класса одним сообщением (например: <code>9А</code>)."
    )


@router.message(AdminFSM.adding_class, F.text, ~F.text.startswith("/"))
async def adm_class_add_text(
    message: Message, state: FSMContext, services: Services
) -> None:
    name = (message.text or "").strip()[:32]
    await services.db.add_class(name)
    await state.clear()
    classes = await services.db.list_classes()
    await message.answer(
        f"✅ Класс <b>{name}</b> добавлен.", reply_markup=classes_admin_kb(classes)
    )


@router.callback_query(F.data.startswith("adm:cls:"))
async def adm_class_subjects(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    part = callback.data.split(":")[2]
    if part == "add":
        return  # обработан выше
    if not _is_admin(services, callback.from_user.id):
        return
    await callback.answer()
    class_id = int(part)
    subjects = await services.db.list_subjects(class_id)
    await state.update_data(class_id=class_id)
    await callback.message.edit_reply_markup(
        reply_markup=subjects_admin_kb(class_id, subjects)
    )


@router.callback_query(F.data.startswith("adm:subj:add:"))
async def adm_subject_add(
    callback: CallbackQuery, state: FSMContext
) -> None:
    class_id = int(callback.data.split(":")[3])
    await callback.answer()
    await state.set_state(AdminFSM.adding_subject)
    await state.update_data(class_id=class_id)
    await callback.message.edit_text(
        "Пришли название предмета одним сообщением (например: <code>Алгебра</code>)."
    )


@router.message(AdminFSM.adding_subject, F.text, ~F.text.startswith("/"))
async def adm_subject_add_text(
    message: Message, state: FSMContext, services: Services
) -> None:
    name = (message.text or "").strip()[:64]
    data = await state.get_data()
    class_id = data["class_id"]
    await services.db.add_subject(class_id, name)
    await state.clear()
    subjects = await services.db.list_subjects(class_id)
    await message.answer(
        f"✅ Предмет <b>{name}</b> добавлен.",
        reply_markup=subjects_admin_kb(class_id, subjects),
    )


@router.callback_query(F.data.startswith("adm:subj:del:"))
async def adm_subject_del(
    callback: CallbackQuery, services: Services
) -> None:
    if not _is_admin(services, callback.from_user.id):
        return
    subject_id = int(callback.data.split(":")[3])
    subject = await services.db.get_subject(subject_id)
    await services.db.delete_subject(subject_id)
    await callback.answer(f"Удалил: {subject['name'] if subject else ''}")
    if subject:
        subjects = await services.db.list_subjects(subject["class_id"])
        await callback.message.edit_reply_markup(
            reply_markup=subjects_admin_kb(subject["class_id"], subjects)
        )

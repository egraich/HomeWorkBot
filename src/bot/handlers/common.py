"""Common handlers: /start, main menu, help, cancel."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import classes_kb, main_menu
from bot.services.container import Services
from bot.states import SessionFSM

router = Router(name="common")

HELP_TEXT = (
    "<b>HomeWorkBot</b> — собирает домашку на завтра и помогает её сделать.\n\n"
    "1️⃣ «Домашка на завтра» → выбери предметы\n"
    "2️⃣ Кидай задания: текст, пересылки, фото с доски/учебника\n"
    "3️⃣ <b>/план</b> — бот разберёт всё и составит план\n"
    "4️⃣ Дальше командуй: «реши 2», «сочинение на тему…», спрашивай что угодно\n"
    "5️⃣ <b>/стоп</b> — завершить, <b>/сброс</b> — начать заново\n\n"
    "Команды: /план · /стоп · /сброс · /стиль · /help"
)


async def show_menu(
    target: Message | CallbackQuery, services: Services, user_id: int
) -> None:
    """Render the main menu, reflecting an active session if one exists."""
    active = await services.db.get_active_session(user_id)
    kb = main_menu(services.cfg.is_admin(user_id), active is not None)
    text = "🏠 <b>Главное меню</b>"
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, services: Services) -> None:
    """Register the user and show the menu or a first-run class picker."""
    user = message.from_user
    await state.clear()
    await services.db.upsert_user(
        user.id, user.username, user.full_name, services.cfg.is_admin(user.id)
    )
    classes = await services.db.list_classes()
    if not classes:
        if services.cfg.is_admin(user.id):
            await message.answer(
                "Бот пустой: сначала добавь класс и предметы.\n"
                "<b>/admin</b> → «Классы и предметы»."
            )
        else:
            await message.answer("Бот ещё настраивается — попроси админа добавить класс.")
        return
    user_class = await services.db.get_user_class(user.id)
    if user_class is None:
        await state.set_state(SessionFSM.choosing_class)
        await state.update_data(after_class="menu")
        await message.answer(
            f"Привет, {user.first_name}! Выбери свой класс:",
            reply_markup=classes_kb(classes),
        )
        return
    await show_menu(message, services, user.id)


@router.message(Command("help", "помощь"))
async def cmd_help(message: Message) -> None:
    """Send the help text."""
    await message.answer(HELP_TEXT)


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery) -> None:
    """Send the help text from the menu button."""
    await callback.answer()
    await callback.message.answer(HELP_TEXT)


@router.callback_query(F.data == "menu")
async def cb_menu(
    callback: CallbackQuery, state: FSMContext, services: Services
) -> None:
    """Reset any FSM state and show the main menu."""
    await state.clear()
    await callback.answer()
    await show_menu(callback, services, callback.from_user.id)


@router.message(Command("cancel", "отмена"))
async def cmd_cancel(
    message: Message, state: FSMContext, services: Services
) -> None:
    """Cancel the current session and return to the main menu."""
    await state.clear()
    active = await services.db.get_active_session(message.from_user.id)
    if active:
        await services.db.set_session_status(active.id, "cancelled")
        await message.answer("Сессия отменена, накопленное забыто.")
    await show_menu(message, services, message.from_user.id)

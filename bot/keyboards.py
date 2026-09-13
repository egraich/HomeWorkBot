"""Инлайн-клавиатуры."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import QUALITY_MODES

MODE_TITLES = {"econ": "🟢 Эконом", "medium": "🟡 Средний", "max": "🔴 Максимальный"}


def main_menu(is_admin: bool, active_session: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if active_session:
        kb.button(text="▶️ Продолжить сессию", callback_data="sess:resume")
        kb.button(text="⏹ Завершить сессию", callback_data="sess:stop")
    else:
        kb.button(text="📚 Домашка на завтра", callback_data="hw:start")
    kb.button(text="❓ Помощь", callback_data="help")
    if is_admin:
        kb.button(text="🛠 Админка", callback_data="adm")
    kb.adjust(1)
    return kb.as_markup()


def classes_kb(classes: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in classes:
        kb.button(text=c["name"], callback_data=f"cls:{c['id']}")
    kb.adjust(2)
    return kb.as_markup()


def subjects_kb(subjects: list, selected: set[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in subjects:
        mark = "✅" if s["id"] in selected else "⬜"
        kb.button(text=f"{mark} {s['name']}", callback_data=f"subj:{s['id']}")
    kb.button(text="✔️ Готово", callback_data="subj:done")
    kb.button(text="⬅️ Отмена", callback_data="menu")
    kb.adjust(2, 2, 2, 2, 1, 1)
    return kb.as_markup()


def collecting_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Составить план", callback_data="sess:plan")
    kb.button(text="⬅️ Отменить", callback_data="sess:reset")
    kb.adjust(1)
    return kb.as_markup()


def dialog_kb(essay_style: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    style_btn = "✒️ Стиль: чистый" if essay_style == "clean" else "✒️ Стиль: живой"
    kb.button(text=style_btn, callback_data="sess:style")
    kb.button(text="⏹ Завершить", callback_data="sess:stop")
    kb.adjust(2)
    return kb.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎚 Режим качества", callback_data="adm:mode")
    kb.button(text="💰 Расходы сегодня", callback_data="adm:spend")
    kb.button(text="📖 Книги", callback_data="adm:books")
    kb.button(text="🏫 Классы и предметы", callback_data="adm:classes")
    kb.button(text="⬅️ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def modes_kb(current: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for mode in QUALITY_MODES:
        mark = "✅" if mode == current else "▫️"
        kb.button(text=f"{mark} {MODE_TITLES[mode]}", callback_data=f"adm:mode:{mode}")
    kb.button(text="⬅️ Админка", callback_data="adm")
    kb.adjust(1)
    return kb.as_markup()


def admin_back_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Админка", callback_data="adm")
    kb.adjust(1)
    return kb.as_markup()


def books_kb(unregistered: list, registered_count: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if unregistered:
        for i, path in enumerate(unregistered):
            kb.button(text=f"➕ {path.name}", callback_data=f"adm:book:{i}")
    kb.button(text=f"📚 Зарегистрированные ({registered_count})", callback_data="adm:book:list")
    kb.button(text="⬅️ Админка", callback_data="adm")
    kb.adjust(1)
    return kb.as_markup()


def bind_subject_kb(subjects: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in subjects:
        kb.button(
            text=f"{s['class_name']} · {s['name']}",
            callback_data=f"adm:bind:{s['id']}",
        )
    kb.button(text="⬅️ Назад", callback_data="adm:books")
    kb.adjust(1)
    return kb.as_markup()


def classes_admin_kb(classes: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in classes:
        kb.button(text=f"🏫 {c['name']}", callback_data=f"adm:cls:{c['id']}")
    kb.button(text="➕ Добавить класс", callback_data="adm:cls:add")
    kb.button(text="⬅️ Админка", callback_data="adm")
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def subjects_admin_kb(class_id: int, subjects: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in subjects:
        kb.button(text=f"➖ {s['name']}", callback_data=f"adm:subj:del:{s['id']}")
    kb.button(text="➕ Добавить предмет", callback_data=f"adm:subj:add:{class_id}")
    kb.button(text="⬅️ Классы", callback_data="adm:classes")
    kb.adjust(2, 1, 1)
    return kb.as_markup()

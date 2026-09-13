"""FSM-состояния бота."""
from aiogram.fsm.state import State, StatesGroup


class SessionFSM(StatesGroup):
    choosing_class = State()
    choosing_subjects = State()
    collecting = State()   # «режим слушания»
    dialog = State()       # после плана


class AdminFSM(StatesGroup):
    adding_class = State()
    adding_subject = State()
    uploading_book = State()  # админ шлёт PDF документом
    binding_book = State()    # выбор предмета для PDF (из папки или из ТГ)

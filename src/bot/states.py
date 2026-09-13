"""FSM states of the bot."""
from aiogram.fsm.state import State, StatesGroup


class SessionFSM(StatesGroup):
    """States of the homework session flow."""

    choosing_class = State()
    choosing_subjects = State()
    collecting = State()
    dialog = State()


class AdminFSM(StatesGroup):
    """States of the admin panel flows."""

    adding_class = State()
    adding_subject = State()
    uploading_book = State()
    binding_book = State()

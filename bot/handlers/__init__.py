from bot.handlers import admin, common, dialog, session

ALL_ROUTERS = [common.router, session.router, dialog.router, admin.router]

__all__ = ["ALL_ROUTERS", "admin", "common", "dialog", "session"]

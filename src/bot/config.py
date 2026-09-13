"""Конфигурация бота из .env (pydantic-settings)."""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

QUALITY_MODES = ("econ", "medium", "max")


def _parse_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            out.append(int(part))
    return out


class Config(BaseSettings):
    # .env ищем и в CWD, и на уровень выше: на проде он лежит в bots/homewbot/,
    # рядом с src (как у соседних ботов)
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram
    bot_token: str = ""
    tg_api_base: str = ""

    # Hack Club AI
    hackclub_api_key: str = ""
    llm_base_url: str = "https://ai.hackclub.com/proxy/v1"

    # Доступ — строки «123,456» из .env, парсим сами
    admin_ids: str = ""
    allowed_ids: str = ""

    # Поведение
    default_mode: str = "econ"
    daily_budget_usd: float = 3.0
    budget_warn: float = 0.8    # доля бюджета, на которой предупреждаем
    budget_block: float = 0.95  # доля бюджета, на которой блокируем платные вызовы

    # Пути
    data_dir: Path = Path("data")

    @field_validator("tg_api_base", mode="after")
    @classmethod
    def _empty_to_none(cls, v: str) -> str | None:
        return v or None

    @field_validator("default_mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in QUALITY_MODES:
            raise ValueError(f"DEFAULT_MODE должен быть одним из {QUALITY_MODES}")
        return v

    @property
    def admin_id_list(self) -> list[int]:
        return _parse_ids(self.admin_ids)

    @property
    def allowed_id_list(self) -> list[int]:
        return _parse_ids(self.allowed_ids)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.sqlite3"

    @property
    def textbooks_dir(self) -> Path:
        return self.data_dir / "textbooks"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.textbooks_dir.mkdir(parents=True, exist_ok=True)

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_id_list

    def is_allowed(self, tg_id: int) -> bool:
        return tg_id in self.admin_id_list or tg_id in self.allowed_id_list

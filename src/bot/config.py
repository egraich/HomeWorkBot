"""Bot configuration loaded from .env (pydantic-settings)."""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

QUALITY_MODES = ("econ", "medium", "max")


def _parse_ids(raw: str | None) -> list[int]:
    """Parse a comma-separated string of telegram ids into a list of ints."""
    if not raw:
        return []
    out: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            out.append(int(part))
    return out


class Config(BaseSettings):
    # .env is looked up in CWD and one level above src, so on a server it
    # can live next to the source folder instead of inside it
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str = ""
    tg_api_base: str = ""

    hackclub_api_key: str = ""
    llm_base_url: str = "https://ai.hackclub.com/proxy/v1"

    admin_ids: str = ""
    allowed_ids: str = ""

    default_mode: str = "econ"
    daily_budget_usd: float = 3.0
    budget_warn: float = 0.8
    budget_block: float = 0.95

    data_dir: Path = Path("data")

    @field_validator("tg_api_base", mode="after")
    @classmethod
    def _empty_to_none(cls, v: str) -> str | None:
        """Treat an empty TG_API_BASE as unset."""
        return v or None

    @field_validator("default_mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        """Validate that the default mode is one of the supported ones."""
        v = v.strip().lower()
        if v not in QUALITY_MODES:
            raise ValueError(f"DEFAULT_MODE must be one of {QUALITY_MODES}")
        return v

    @property
    def admin_id_list(self) -> list[int]:
        """Return admin telegram ids as a list of ints."""
        return _parse_ids(self.admin_ids)

    @property
    def allowed_id_list(self) -> list[int]:
        """Return whitelisted telegram ids as a list of ints."""
        return _parse_ids(self.allowed_ids)

    @property
    def db_path(self) -> Path:
        """Return the SQLite database path."""
        return self.data_dir / "bot.sqlite3"

    @property
    def textbooks_dir(self) -> Path:
        """Return the textbooks storage path."""
        return self.data_dir / "textbooks"

    def ensure_dirs(self) -> None:
        """Create the data and textbooks directories if missing."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.textbooks_dir.mkdir(parents=True, exist_ok=True)

    def is_admin(self, tg_id: int) -> bool:
        """Return True if the user id is an admin."""
        return tg_id in self.admin_id_list

    def is_allowed(self, tg_id: int) -> bool:
        """Return True if the user id may use the bot."""
        return tg_id in self.admin_id_list or tg_id in self.allowed_id_list

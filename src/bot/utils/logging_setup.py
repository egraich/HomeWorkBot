"""Centralized logging configuration for the whole bot."""
from __future__ import annotations

import logging
import logging.config

VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging, per-library levels and the console handler."""
    level = level.upper() if level.upper() in VALID_LEVELS else "INFO"
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {"format": FORMAT, "datefmt": "%Y-%m-%d %H:%M:%S"},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"level": level, "handlers": ["console"]},
        }
    )
    # noisy third-party libraries: keep only what matters
    for name in ("httpx", "httpx2", "httpcore", "aiosqlite", "asyncio", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # aiogram's own event log: received updates and handler bindings
    logging.getLogger("aiogram.event").setLevel(logging.INFO)
    logging.getLogger("aiogram.client.session").setLevel(logging.WARNING)

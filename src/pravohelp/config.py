from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    database_url: str
    log_level: str
    lawyer_name: str
    lawyer_telegram: str
    lawyer_phone: str
    lawyer_specialization: str
    admin_telegram_ids: tuple[int, ...]
    max_scenarios_per_hour: int


def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        return ()
    return tuple(int(x.strip()) for x in raw.split(",") if x.strip())


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не задано. Скопіюй .env.example → .env і встав токен від @BotFather."
        )

    return Settings(
        telegram_bot_token=token,
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/pravohelp.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        lawyer_name=os.getenv("LAWYER_NAME", "Дмитро Глушко"),
        lawyer_telegram=os.getenv("LAWYER_TELEGRAM", ""),
        lawyer_phone=os.getenv("LAWYER_PHONE", ""),
        lawyer_specialization=os.getenv("LAWYER_SPECIALIZATION", ""),
        admin_telegram_ids=_parse_admin_ids(os.getenv("ADMIN_TELEGRAM_IDS", "")),
        max_scenarios_per_hour=int(os.getenv("MAX_SCENARIOS_PER_HOUR", "5")),
    )

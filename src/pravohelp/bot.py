from __future__ import annotations

import logging
import sys

import structlog
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

from pravohelp.config import load_settings
from pravohelp.handlers.start import (
    cmd_help,
    cmd_start,
    on_about,
    on_disclaimer_accept,
    on_disclaimer_decline,
)
from pravohelp.storage.db import init_db


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level, logging.INFO),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
    )


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    app.add_handler(CallbackQueryHandler(on_disclaimer_accept, pattern=r"^disclaimer:accept$"))
    app.add_handler(CallbackQueryHandler(on_disclaimer_decline, pattern=r"^disclaimer:decline$"))
    app.add_handler(CallbackQueryHandler(on_about, pattern=r"^info:about$"))

    return app


def main() -> None:
    settings = load_settings()
    _configure_logging(settings.log_level)
    init_db(settings.database_url)

    log = structlog.get_logger(__name__)
    log.info("bot_starting", level=settings.log_level)

    app = build_application(settings.telegram_bot_token)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

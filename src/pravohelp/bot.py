from __future__ import annotations

import logging
import sys
import warnings

import structlog
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)
from telegram.warnings import PTBUserWarning

# Свідоме рішення per_message=False у salary ConversationHandler.
warnings.filterwarnings(
    "ignore",
    message="If 'per_message=False'",
    category=PTBUserWarning,
)

from pravohelp.config import load_settings
from pravohelp.document.generator import cleanup_old_documents
from pravohelp.handlers.salary import build_salary_conversation
from pravohelp.storage.drafts import cleanup_old_drafts
from pravohelp.handlers.start import (
    cmd_cancel_global,
    cmd_help,
    cmd_menu,
    cmd_start,
    on_about,
    on_disclaimer_accept,
    on_disclaimer_decline,
)
from pravohelp.storage.db import init_db

CLEANUP_INTERVAL_SECONDS = 600


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


async def _cleanup_job(_context) -> None:
    cleanup_old_documents()
    cleanup_old_drafts()


async def _error_handler(update: object, context) -> None:
    log = structlog.get_logger(__name__)
    log.error(
        "unhandled_exception",
        error=str(context.error),
        error_type=type(context.error).__name__,
        update=str(update)[:500] if update else None,
        exc_info=context.error,
    )


BOT_COMMANDS = [
    BotCommand("menu", "Головне меню зі сценаріями"),
    BotCommand("start", "Перший запуск і умови"),
    BotCommand("help", "Допомога і FAQ"),
    BotCommand("cancel", "Вийти з поточного сценарію"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)


def build_application(token: str) -> Application:
    app = Application.builder().token(token).post_init(_post_init).build()

    # ConversationHandler має бути зареєстрований ПЕРЕД одиничними CallbackQueryHandler-ами,
    # щоб entry_point на "scenario:salary" зловив подію раніше за загальний список.
    app.add_handler(build_salary_conversation())

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    # Глобальний /cancel — обробляє випадок, коли користувач не в ConversationHandler.
    # Всередині сценарію fallback з ConversationHandler перехопить /cancel раніше.
    app.add_handler(CommandHandler("cancel", cmd_cancel_global))

    app.add_handler(CallbackQueryHandler(on_disclaimer_accept, pattern=r"^disclaimer:accept$"))
    app.add_handler(CallbackQueryHandler(on_disclaimer_decline, pattern=r"^disclaimer:decline$"))
    app.add_handler(CallbackQueryHandler(on_about, pattern=r"^info:about$"))

    if app.job_queue is not None:
        app.job_queue.run_repeating(
            _cleanup_job, interval=CLEANUP_INTERVAL_SECONDS, first=CLEANUP_INTERVAL_SECONDS
        )

    app.add_error_handler(_error_handler)

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

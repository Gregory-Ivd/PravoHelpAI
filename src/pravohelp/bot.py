from __future__ import annotations

import logging
import sys
import warnings

import structlog
from telegram import BotCommand, BotCommandScopeChat, Update
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
from pravohelp.handlers.admin import cmd_stats
from pravohelp.handlers.consultation import build_consultation_conversation
from pravohelp.handlers.history import cmd_history
from pravohelp.handlers.salary import (
    build_salary_conversation,
    on_post_edit_hint,
    on_post_lawyer_review,
)
from pravohelp.handlers.start import (
    cmd_cancel_global,
    cmd_help,
    cmd_menu,
    cmd_start,
    on_consult_field,
    on_disclaimer_accept,
    on_disclaimer_decline,
    on_main_consult,
    on_main_home,
    on_main_scenarios,
)
from pravohelp.storage.backup import backup_db, cleanup_old_backups
from pravohelp.storage.db import init_db
from pravohelp.storage.drafts import cleanup_old_drafts

CLEANUP_INTERVAL_SECONDS = 600
BACKUP_INTERVAL_SECONDS = 86400  # раз на добу


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


async def _backup_job(_context) -> None:
    backup_db()
    cleanup_old_backups()


async def _error_handler(update: object, context) -> None:
    log = structlog.get_logger(__name__)
    err = context.error
    log.error(
        "unhandled_exception",
        error=str(err),
        error_type=type(err).__name__,
        update=str(update)[:500] if update else None,
        exc_info=err,
    )

    # Сповіщаємо першого адміна. Якщо саме надсилання падає — лише логуємо.
    settings = load_settings()
    if not settings.admin_telegram_ids:
        return
    admin_id = settings.admin_telegram_ids[0]

    import traceback as _tb
    from html import escape

    tb_text = "".join(_tb.format_exception(type(err), err, err.__traceback__))[-1500:]
    msg = (
        f"⚠️ <b>Bot exception</b>\n"
        f"<code>{escape(type(err).__name__)}: {escape(str(err)[:200])}</code>\n\n"
        f"<pre>{escape(tb_text)}</pre>"
    )
    try:
        await context.bot.send_message(chat_id=admin_id, text=msg, parse_mode="HTML")
    except Exception:
        log.exception("admin_notification_failed", admin_id=admin_id)


BOT_COMMANDS = [
    BotCommand("menu", "Головне меню зі сценаріями"),
    BotCommand("start", "Перший запуск і умови"),
    BotCommand("history", "Мої минулі запити"),
    BotCommand("help", "Допомога і FAQ"),
    BotCommand("cancel", "Вийти з поточного сценарію"),
]

ADMIN_BOT_COMMANDS = BOT_COMMANDS + [
    BotCommand("stats", "📊 Статистика бота (адмін)"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)
    for admin_id in load_settings().admin_telegram_ids:
        try:
            await app.bot.set_my_commands(
                ADMIN_BOT_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception:
            structlog.get_logger(__name__).exception(
                "admin_commands_set_failed", admin_id=admin_id
            )


def build_application(token: str) -> Application:
    app = Application.builder().token(token).post_init(_post_init).build()

    # ConversationHandler-и реєструються ПЕРЕД одиничними CallbackQueryHandler-ами.
    app.add_handler(build_salary_conversation())
    app.add_handler(build_consultation_conversation())

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    # Глобальний /cancel — обробляє випадок, коли користувач не в ConversationHandler.
    # Всередині сценарію fallback з ConversationHandler перехопить /cancel раніше.
    app.add_handler(CommandHandler("cancel", cmd_cancel_global))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("stats", cmd_stats))

    app.add_handler(CallbackQueryHandler(on_disclaimer_accept, pattern=r"^disclaimer:accept$"))
    app.add_handler(CallbackQueryHandler(on_disclaimer_decline, pattern=r"^disclaimer:decline$"))
    app.add_handler(CallbackQueryHandler(on_main_home, pattern=r"^main:home$"))
    app.add_handler(CallbackQueryHandler(on_main_scenarios, pattern=r"^main:scenarios$"))
    app.add_handler(CallbackQueryHandler(on_main_consult, pattern=r"^main:consult$"))
    app.add_handler(CallbackQueryHandler(on_consult_field, pattern=r"^consult_field:"))
    app.add_handler(CallbackQueryHandler(on_post_lawyer_review, pattern=r"^post:lawyer_review$"))
    app.add_handler(CallbackQueryHandler(on_post_edit_hint, pattern=r"^post:edit_hint$"))

    if app.job_queue is not None:
        app.job_queue.run_repeating(
            _cleanup_job, interval=CLEANUP_INTERVAL_SECONDS, first=CLEANUP_INTERVAL_SECONDS
        )
        app.job_queue.run_repeating(
            _backup_job, interval=BACKUP_INTERVAL_SECONDS, first=60
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

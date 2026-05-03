from __future__ import annotations

from datetime import datetime, timezone

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from pravohelp.storage.db import get_session
from pravohelp.storage.models import User

log = structlog.get_logger(__name__)


DISCLAIMER = (
    "⚖️ <b>PravoHelpAI — генератор юридичних шаблонів</b>\n\n"
    "Бот допомагає підготувати документ для типових ситуацій. "
    "Він <b>не замінює юридичну консультацію</b>.\n\n"
    "<b>Що важливо знати:</b>\n"
    "• Згенерований документ — це <b>шаблон</b>, який потребує перевірки під твою конкретну ситуацію.\n"
    "• Перед поданням у держоргани або суд рекомендуємо перевірку юристом.\n"
    "• Розробник не несе відповідальності за результати використання документа.\n"
    "• Військове законодавство України оновлюється часто — для питань мобілізації перевір актуальність на rada.gov.ua.\n\n"
    "Натиснувши «Згоден», ти підтверджуєш, що прочитав/-ла і розумієш ці умови."
)


MAIN_MENU_TEXT = (
    "Обери ситуацію:\n\n"
    "💰 <b>Невиплата зарплати</b> — досудова претензія, скарга до Держпраці, позов до суду.\n"
    "🚧 Інші сценарії (повістка, штраф ПДР) — у розробці."
)


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 Невиплата зарплати", callback_data="scenario:salary")],
            [InlineKeyboardButton("ℹ️ Про бота", callback_data="info:about")],
        ]
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    tg_user = update.effective_user

    with get_session() as session:
        user = session.query(User).filter_by(telegram_id=tg_user.id).one_or_none()
        if user is None:
            user = User(telegram_id=tg_user.id, username=tg_user.username)
            session.add(user)
            log.info("new_user", telegram_id=tg_user.id)

        already_accepted = user.disclaimer_accepted_at is not None

    if already_accepted:
        await update.message.reply_html(MAIN_MENU_TEXT, reply_markup=_main_menu_keyboard())
        return

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Згоден, продовжити", callback_data="disclaimer:accept")],
            [InlineKeyboardButton("❌ Відмовитись", callback_data="disclaimer:decline")],
        ]
    )
    await update.message.reply_html(DISCLAIMER, reply_markup=keyboard)


async def on_disclaimer_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()

    with get_session() as session:
        user = session.query(User).filter_by(telegram_id=update.effective_user.id).one_or_none()
        if user is not None:
            user.disclaimer_accepted_at = datetime.now(timezone.utc)

    await query.edit_message_text(
        MAIN_MENU_TEXT, parse_mode="HTML", reply_markup=_main_menu_keyboard()
    )


async def on_disclaimer_decline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text(
        "Зрозуміло. Без згоди з умовами бот не може продовжити.\n"
        "Якщо передумаєш — натисни /start ще раз."
    )


async def on_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text(
        "<b>PravoHelpAI</b> — Telegram-бот для підготовки юридичних документів "
        "по поширених ситуаціях в Україні.\n\n"
        "Поки доступний один сценарій: <b>невиплата зарплати</b>. "
        "Скоро додамо «повістка» і «штраф ПДР».\n\n"
        "Команди:\n"
        "/start — головне меню\n"
        "/help — допомога\n"
        "/cancel — вийти з поточного сценарію\n\n"
        "Натисни /start щоб обрати сценарій.",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_html(
        "<b>Як користуватись ботом</b>\n\n"
        "1. /start — обрати ситуацію.\n"
        "2. Бот ставить уточнюючі питання — відповідай по черзі.\n"
        "3. У кінці отримаєш готовий документ (DOCX) і чек-лист дій.\n"
        "4. Опціонально — звернись за консультацією до юриста-партнера.\n\n"
        "Команди:\n"
        "/start — головне меню\n"
        "/cancel — вийти з поточного сценарію\n"
        "/help — це повідомлення"
    )

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


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    with get_session() as session:
        user = session.query(User).filter_by(telegram_id=update.effective_user.id).one_or_none()
        accepted = user is not None and user.disclaimer_accepted_at is not None

    if not accepted:
        await update.message.reply_text(
            "Спочатку натисни /start і прийми умови — без цього бот не може працювати."
        )
        return

    await update.message.reply_html(MAIN_MENU_TEXT, reply_markup=_main_menu_keyboard())


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


async def cmd_cancel_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальний /cancel — спрацьовує, коли користувач не в ConversationHandler."""
    if update.message is None:
        return
    await update.message.reply_text(
        "Зараз ти не в сценарії — нема чого скасовувати. Натисни /menu щоб обрати ситуацію."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_html(
        "<b>Як користуватись ботом</b>\n\n"
        "1. /start — прийняти умови (один раз).\n"
        "2. /menu — обрати ситуацію.\n"
        "3. Бот ставить уточнюючі питання — відповідай по черзі.\n"
        "4. У кінці отримаєш готовий документ (DOCX) і чек-лист дій.\n"
        "5. Опціонально — звернись за консультацією до юриста-партнера.\n\n"
        "<b>Команди</b>\n"
        "/start — перший запуск і дисклеймер\n"
        "/menu — головне меню зі сценаріями\n"
        "/cancel — вийти з поточного сценарію\n"
        "/help — це повідомлення\n\n"
        "<b>FAQ</b>\n\n"
        "<b>Помилився у відповіді — як виправити?</b>\n"
        "Введи /cancel і запусти сценарій з /menu заново. Покрокового бектреку поки немає.\n\n"
        "<b>Чи зберігаються мої дані?</b>\n"
        "У базі бота — лише факт використання (telegram_id, обраний сценарій, дата). "
        "ПІБ, ІПН, адреса, телефон у базу не пишуться. "
        "Згенерований DOCX автоматично видаляється з сервера через ~1 годину — "
        "встигни завантажити.\n\n"
        "<b>Документ — це готовий папір?</b>\n"
        "Це шаблон. Перед поданням у держоргани/суд перевір під свою ситуацію — "
        "або сам, або з юристом. Бот не несе відповідальності за результат.\n\n"
        "<b>Як отримати консультацію юриста?</b>\n"
        "Після завершення сценарію бот покаже контакти юриста-партнера. "
        "Звертатись напряму — умови узгоджуєш з юристом.\n\n"
        "<b>Чи безпечно вводити персональні дані в Telegram?</b>\n"
        "Telegram шифрує переписку клієнт-сервер. Бот не передає твої дані третім сторонам. "
        "Якщо параноя — введи мінімум обовʼязкового і доповни вручну в DOCX."
    )

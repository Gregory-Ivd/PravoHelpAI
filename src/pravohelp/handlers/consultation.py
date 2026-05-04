"""ConversationHandler для запису на консультацію юриста.

FSM збирає 3 поля: ПІБ, телефон, опис ситуації. Галузь права передається через
context.user_data перед запуском (з consultation menu або з pre-generate блоку).
Після підтвердження — заявка пишеться в БД і надсилається юристу в Telegram.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum
from typing import Any

import structlog
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from pravohelp.config import load_settings
from pravohelp.storage.db import get_session
from pravohelp.storage.models import ConsultationRequest
from pravohelp.utils.validators import (
    ValidationError,
    validate_phone,
    validate_text,
)

log = structlog.get_logger(__name__)

CONSULT_KEY = "consult"
FIELD_KEY = "consult_field"  # ключ у user_data для коду галузі права


FIELD_LABELS: dict[str, str] = {
    "family": "Сімейне право",
    "labor": "Трудове право",
    "military": "Військове право",
    "medical": "Медичне та соціальне право",
    "tax": "Податкове право",
    "inheritance": "Спадкове право",
    "international": "Міжнародні питання",
    "admin": "Адміністративне право (ПДР)",
    "other": "Інші питання",
}


class C(IntEnum):
    NAME = 200
    PHONE = 201
    DESCRIPTION = 202
    CONFIRM = 203


def _data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    if context.user_data is None:
        raise RuntimeError("user_data is None")
    return context.user_data.setdefault(CONSULT_KEY, {})


# ============================================================================
# Entry — виклик з меню галузі або pre-generate блоку
# ============================================================================


async def start_consultation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or context.user_data is None:
        return ConversationHandler.END
    await query.answer()

    # Витягаємо код галузі з callback_data: "consult_start:<field>"
    if query.data is None or ":" not in query.data:
        return ConversationHandler.END
    field = query.data.split(":", 1)[1]
    if field not in FIELD_LABELS:
        return ConversationHandler.END

    context.user_data[CONSULT_KEY] = {"field": field, "started_at": datetime.now(UTC)}

    label = FIELD_LABELS[field]
    if query.message is not None:
        await query.message.reply_html(
            f"📩 <b>Запис на консультацію</b>\n"
            f"Галузь: <i>{label}</i>\n\n"
            "Я задам 3 коротких питання — це займе ~1 хв.\n"
            "Будь-коли можна вийти командою /cancel.\n\n"
            "<b>1/3.</b> Ваше ПІБ повністю.\n"
            "Як у паспорті. Наприклад: <i>Іваненко Олександр Сергійович</i>.",
            reply_markup=ReplyKeyboardRemove(),
        )
    return C.NAME


# ============================================================================
# State handlers
# ============================================================================


async def on_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return C.NAME
    try:
        value = validate_text(update.message.text, min_len=5, max_len=150, label="ПІБ")
    except ValidationError as e:
        await update.message.reply_text(f"⚠️ {e}\n\nСпробуй ще раз або /cancel.")
        return C.NAME
    _data(context)["name"] = value

    await update.message.reply_html(
        "<b>2/3.</b> Ваш телефон.\n"
        "У форматі <code>+380XXXXXXXXX</code> або <code>0XXXXXXXXX</code>."
    )
    return C.PHONE


async def on_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return C.PHONE
    try:
        value = validate_phone(update.message.text)
    except ValidationError as e:
        await update.message.reply_text(f"⚠️ {e}\n\nСпробуй ще раз або /cancel.")
        return C.PHONE
    _data(context)["phone"] = value

    await update.message.reply_html(
        "<b>3/3.</b> Коротко опишіть вашу ситуацію — у чому суть і що потрібно.\n\n"
        "До 1000 символів. Юрист побачить це повідомлення."
    )
    return C.DESCRIPTION


async def on_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return C.DESCRIPTION
    try:
        value = validate_text(update.message.text, min_len=10, max_len=1000, label="Опис")
    except ValidationError as e:
        await update.message.reply_text(f"⚠️ {e}\n\nСпробуй ще раз або /cancel.")
        return C.DESCRIPTION
    data = _data(context)
    data["description"] = value

    summary = (
        "<b>📋 Перевір заявку перед відправкою</b>\n\n"
        f"• Галузь: {FIELD_LABELS.get(data['field'], data['field'])}\n"
        f"• ПІБ: {data['name']}\n"
        f"• Телефон: {data['phone']}\n"
        f"• Опис: {data['description']}\n\n"
        "Якщо все правильно — надсилаємо."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Надіслати юристу", callback_data="consult:send")],
            [InlineKeyboardButton("❌ Скасувати", callback_data="consult:cancel")],
        ]
    )
    await update.message.reply_html(summary, reply_markup=keyboard)
    return C.CONFIRM


# ============================================================================
# Confirm + dispatch
# ============================================================================


async def on_confirm_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return ConversationHandler.END
    await query.answer()

    data = _data(context)
    user_id = update.effective_user.id
    settings = load_settings()

    # 1. Зберігаємо в БД.
    request_id = _save_request(
        telegram_id=user_id,
        name=data["name"],
        phone=data["phone"],
        field=data["field"],
        description=data["description"],
    )

    # 2. Намагаємось надіслати Дмитру.
    dispatched = False
    if settings.lawyer_telegram_id:
        try:
            tg_user = update.effective_user
            user_link = f"@{tg_user.username}" if tg_user.username else f"id={tg_user.id}"
            push_text = (
                "📩 <b>Нова заявка на консультацію</b>\n\n"
                f"<b>Галузь:</b> {FIELD_LABELS.get(data['field'], data['field'])}\n"
                f"<b>ПІБ:</b> {data['name']}\n"
                f"<b>Телефон:</b> {data['phone']}\n"
                f"<b>Telegram:</b> {user_link}\n\n"
                f"<b>Опис ситуації:</b>\n{data['description']}\n\n"
                f"<i>Заявка #{request_id} • {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}</i>"
            )
            await context.bot.send_message(
                chat_id=settings.lawyer_telegram_id,
                text=push_text,
                parse_mode="HTML",
            )
            dispatched = True
            _mark_dispatched(request_id)
        except Exception:
            log.exception("lawyer_push_failed", request_id=request_id)

    # 3. Підтвердження користувачу.
    if query.message is not None:
        if dispatched:
            text = (
                "✅ <b>Заявку надіслано юристу.</b>\n\n"
                f"{settings.lawyer_name} звʼяжеться з вами за номером {data['phone']} "
                "найближчим часом.\n\n"
                f"Якщо потрібно швидше — напишіть напряму: {settings.lawyer_telegram}"
            )
        else:
            text = (
                "✅ <b>Заявку прийнято.</b>\n\n"
                f"Юрист {settings.lawyer_name} отримає її і звʼяжеться з вами за "
                f"номером {data['phone']}.\n\n"
                f"Можете також написати йому напряму: {settings.lawyer_telegram}"
            )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 До головного меню", callback_data="main:home")]]
        )
        await query.message.reply_html(text, reply_markup=keyboard)

    context.user_data.pop(CONSULT_KEY, None)
    return ConversationHandler.END


async def on_confirm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()
    if context.user_data is not None:
        context.user_data.pop(CONSULT_KEY, None)
    if query.message is not None:
        await query.message.reply_text(
            "Ок, заявку скасовано. /menu — головне меню."
        )
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data is not None:
        context.user_data.pop(CONSULT_KEY, None)
    if update.message is not None:
        await update.message.reply_text("Заявку скасовано. /menu — головне меню.")
    return ConversationHandler.END


# ============================================================================
# DB helpers
# ============================================================================


def _save_request(*, telegram_id: int, name: str, phone: str, field: str, description: str) -> int:
    with get_session() as session:
        req = ConsultationRequest(
            telegram_id=telegram_id,
            name=name,
            phone=phone,
            field=field,
            description=description,
        )
        session.add(req)
        session.flush()
        return req.id


def _mark_dispatched(request_id: int) -> None:
    with get_session() as session:
        req = session.get(ConsultationRequest, request_id)
        if req is not None:
            req.dispatched_at = datetime.now(UTC)


# ============================================================================
# ConversationHandler factory
# ============================================================================


def build_consultation_conversation() -> ConversationHandler:
    text_filter = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_consultation, pattern=r"^consult_start:"),
        ],
        states={
            C.NAME: [MessageHandler(text_filter, on_name)],
            C.PHONE: [MessageHandler(text_filter, on_phone)],
            C.DESCRIPTION: [MessageHandler(text_filter, on_description)],
            C.CONFIRM: [
                CallbackQueryHandler(on_confirm_send, pattern=r"^consult:send$"),
                CallbackQueryHandler(on_confirm_cancel, pattern=r"^consult:cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="consultation",
        persistent=False,
        per_message=False,
    )

"""ConversationHandler для сценарію «невиплата зарплати»."""

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
from pravohelp.document.generator import render
from pravohelp.storage.db import get_session
from pravohelp.storage.drafts import delete_draft, load_draft, save_draft
from pravohelp.storage.models import ScenarioRequest, User
from pravohelp.utils.funnel import emit as funnel_emit
from pravohelp.utils.rate_limit import check_and_record
from pravohelp.utils.validators import (
    ValidationError,
    format_amount_uah,
    format_date,
    format_month_year,
    now_date_str,
    validate_amount_uah,
    validate_date,
    validate_edrpou,
    validate_month_year,
    validate_phone,
    validate_tax_id,
    validate_text,
)

log = structlog.get_logger(__name__)


class S(IntEnum):
    EMPLOYER_NAME = 100
    EMPLOYER_EDRPOU = 101
    EMPLOYER_ADDRESS = 102
    AMOUNT = 103
    PERIOD_FROM = 104
    PERIOD_TO = 105
    LAST_PAYMENT_DATE = 106
    USER_NAME = 107
    USER_TAX_ID = 108
    USER_ADDRESS = 109
    USER_PHONE = 110
    PLAN_CHOICE = 111
    RESUME_PROMPT = 112
    PREVIEW = 113
    EDIT_FIELD_CHOICE = 114
    PRE_GENERATE = 115


SCENARIO = "salary"
EDITING_KEY = f"{SCENARIO}_editing"


# Поля для прев'ю/редагування: (key in data, ярлик, state-куди-повернути для редагування).
FIELDS = [
    ("employer_name", "Роботодавець", S.EMPLOYER_NAME),
    ("employer_edrpou", "ЄДРПОУ", S.EMPLOYER_EDRPOU),
    ("employer_address", "Адреса роботодавця", S.EMPLOYER_ADDRESS),
    ("amount", "Сума", S.AMOUNT),
    ("period_from", "Період з", S.PERIOD_FROM),
    ("period_to", "Період по", S.PERIOD_TO),
    ("last_payment_date", "Остання виплата", S.LAST_PAYMENT_DATE),
    ("user_name", "Твоє ПІБ", S.USER_NAME),
    ("user_tax_id", "ІПН", S.USER_TAX_ID),
    ("user_address", "Твоя адреса", S.USER_ADDRESS),
    ("user_phone", "Телефон", S.USER_PHONE),
]


QUESTIONS: dict[int, str] = {
    S.EMPLOYER_NAME: (
        "<b>1/11.</b> Як називається організація-роботодавець?\n\n"
        "Введи точну назву так, як вона у трудовому договорі. "
        "Наприклад: <i>ТОВ «Промінь»</i> або <i>ФОП Іваненко Іван Іванович</i>."
    ),
    S.EMPLOYER_EDRPOU: (
        "<b>2/11.</b> ЄДРПОУ роботодавця (8 цифр).\n\n"
        "Знайдеш у трудовому договорі або на сайті <a href='https://usr.minjust.gov.ua/'>"
        "Єдиного держреєстру</a>.\n\n"
        "Якщо не знаєш — напиши <b>не знаю</b>."
    ),
    S.EMPLOYER_ADDRESS: (
        "<b>3/11.</b> Юридична адреса роботодавця.\n\n"
        "Як вона у договорі або в ЄДР. Наприклад: "
        "<i>04050, м. Київ, вул. Січових Стрільців, 50, оф. 12</i>."
    ),
    S.AMOUNT: (
        "<b>4/11.</b> Сума заборгованості (у гривнях).\n\n"
        "Вкажи число — наприклад <code>15000</code> або <code>23500.50</code>."
    ),
    S.PERIOD_FROM: (
        "<b>5/11.</b> З якого місяця почалась заборгованість?\n\n"
        "Формат: <code>ММ.РРРР</code>. Наприклад: <code>01.2026</code> (січень 2026)."
    ),
    S.PERIOD_TO: (
        "<b>6/11.</b> По який місяць триває заборгованість?\n\n"
        "Формат: <code>ММ.РРРР</code>. Якщо лише один місяць — повтори той самий, "
        "що в попередньому питанні."
    ),
    S.LAST_PAYMENT_DATE: (
        "<b>7/11.</b> Дата останньої виплати зарплати.\n\n"
        "Формат: <code>ДД.ММ.РРРР</code>. Наприклад: <code>15.12.2025</code>.\n"
        "Якщо ніколи не отримував — введи дату початку роботи."
    ),
    S.USER_NAME: (
        "<b>8/11.</b> Твоє ПІБ повністю.\n\n"
        "Як у паспорті. Наприклад: <i>Іваненко Олександр Сергійович</i>."
    ),
    S.USER_TAX_ID: (
        "<b>9/11.</b> Твій ІПН (РНОКПП) — 10 цифр.\n\nБез пробілів і дефісів."
    ),
    S.USER_ADDRESS: (
        "<b>10/11.</b> Твоя адреса для листування.\n\n"
        "Куди має прийти відповідь. Наприклад: "
        "<i>03150, м. Київ, вул. Велика Васильківська, 100, кв. 25</i>."
    ),
    S.USER_PHONE: (
        "<b>11/11.</b> Твій телефон.\n\n"
        "У форматі <code>+380XXXXXXXXX</code> або <code>0XXXXXXXXX</code>."
    ),
}


# ============================================================================
# Helpers
# ============================================================================


def _data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    if context.user_data is None:
        raise RuntimeError("user_data is None — це не має статись у звичайному діалозі.")
    return context.user_data.setdefault(SCENARIO, {})


async def _send_question(update: Update, text: str, *, html: bool = True) -> None:
    if update.message is None:
        return
    if html:
        await update.message.reply_html(text, reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text(text, reply_markup=ReplyKeyboardRemove())


async def _send_error(update: Update, error: ValidationError) -> None:
    if update.message is None:
        return
    await update.message.reply_text(f"⚠️ {error}\n\nСпробуй ще раз або /cancel щоб вийти.")


def _persist(update: Update, context: ContextTypes.DEFAULT_TYPE, next_state: int) -> None:
    """Зберегти прогрес чернетки. Викликати ПІСЛЯ оновлення _data(context)."""
    if update.effective_user is None:
        return
    try:
        save_draft(update.effective_user.id, SCENARIO, next_state, _data(context))
    except Exception:
        log.exception("draft_save_failed", state=next_state)


async def _advance_or_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    next_state: int,
    *,
    completed_field: str | None = None,
) -> int:
    """Якщо це редагування одного поля — повертаємось на прев'ю; інакше — наступний крок."""
    tg_id = update.effective_user.id if update.effective_user else None
    editing = context.user_data.pop(EDITING_KEY, None)

    if completed_field is not None and tg_id is not None:
        funnel_emit(
            "salary_step", telegram_id=tg_id, field=completed_field, editing=editing is not None
        )

    if editing is not None:
        _persist(update, context, S.PREVIEW)
        funnel_emit("salary_preview_shown", telegram_id=tg_id, source="edit")
        await _send_preview(update, context)
        return S.PREVIEW

    _persist(update, context, next_state)
    if next_state == S.PREVIEW:
        funnel_emit("salary_preview_shown", telegram_id=tg_id, source="flow")
        await _send_preview(update, context)
    elif next_state in QUESTIONS:
        await _send_question(update, QUESTIONS[next_state])
    return next_state


def _format_field(key: str, data: dict[str, Any]) -> str:
    value = data.get(key)
    if value is None:
        return "—"
    if key == "amount":
        return format_amount_uah(value)
    if key in ("period_from", "period_to"):
        return format_month_year(*value)
    if key == "last_payment_date":
        return format_date(value)
    return str(value)


async def _send_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = update.message
    if target is None and update.callback_query is not None:
        target = update.callback_query.message
    if target is None:
        return

    data = _data(context)
    lines = [f"• <b>{label}:</b> {_format_field(key, data)}" for key, label, _ in FIELDS]
    text = (
        "<b>📋 Перевір дані перед генерацією</b>\n\n"
        + "\n".join(lines)
        + "\n\nЯкщо все правильно — генеруємо документи. "
        "Знайшов помилку — виправ окреме поле."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Згенерувати документи", callback_data="preview:confirm")],
            [InlineKeyboardButton("✏️ Виправити поле", callback_data="preview:edit")],
            [InlineKeyboardButton("❌ Скасувати", callback_data="preview:cancel")],
        ]
    )
    await target.reply_html(text, reply_markup=keyboard)


# ============================================================================
# Entry point — викликається з callback "scenario:salary"
# ============================================================================


async def start_salary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    if context.user_data is None or update.effective_user is None:
        return ConversationHandler.END

    settings = load_settings()
    allowed, retry_after = check_and_record(
        update.effective_user.id, settings.max_scenarios_per_hour
    )
    if not allowed:
        minutes = max(retry_after // 60, 1)
        await query.edit_message_text(
            f"⏳ Ти вже запускав сценарій {settings.max_scenarios_per_hour} разів за останню годину. "
            f"Спробуй ще раз через ~{minutes} хв.\n\n"
            "Це обмеження проти спаму — реальним користувачам більше і не треба."
        )
        return ConversationHandler.END

    funnel_emit("salary_started", telegram_id=update.effective_user.id)

    draft = load_draft(update.effective_user.id, SCENARIO)
    if draft is not None:
        saved_state, saved_data = draft
        progress = _progress_label(saved_state)
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶️ Продовжити", callback_data="salary_resume:yes")],
                [InlineKeyboardButton("🔄 Почати наново", callback_data="salary_resume:no")],
            ]
        )
        await query.edit_message_text(
            "<b>💰 Невиплата зарплати</b>\n\n"
            f"У тебе вже є незавершена чернетка ({progress}). "
            "Що робимо?",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        # Тимчасово зберігаємо чернетку в context для resume-handler-а
        context.user_data[f"{SCENARIO}_pending_resume"] = (saved_state, saved_data)
        return S.RESUME_PROMPT

    return await _begin_fresh(query, context)


def _progress_label(state: int) -> str:
    if state == S.PLAN_CHOICE:
        return "усі дані зібрано, треба обрати план"
    if state == S.PREVIEW:
        return "усі дані зібрано, потрібен фінальний перегляд"
    order = [
        S.EMPLOYER_NAME,
        S.EMPLOYER_EDRPOU,
        S.EMPLOYER_ADDRESS,
        S.AMOUNT,
        S.PERIOD_FROM,
        S.PERIOD_TO,
        S.LAST_PAYMENT_DATE,
        S.USER_NAME,
        S.USER_TAX_ID,
        S.USER_ADDRESS,
        S.USER_PHONE,
    ]
    try:
        idx = order.index(state)
    except ValueError:
        return "невідомий етап"
    return f"крок {idx + 1}/11"


async def _begin_fresh(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Початок з нуля. query — callback_query від кнопки entry або 'почати наново'."""
    context.user_data[SCENARIO] = {"started_at": datetime.now(UTC)}
    context.user_data.pop(f"{SCENARIO}_pending_resume", None)

    await query.edit_message_text(
        "<b>💰 Невиплата зарплати</b>\n\n"
        "Я задам кілька запитань про твою ситуацію — це займе ~3 хвилини. "
        "На основі відповідей сформую документи.\n\n"
        "Будь-коли можна вийти командою /cancel.\n\n"
        "<i>Поїхали. Перше питання нижче.</i>",
        parse_mode="HTML",
    )

    if query.message is None:
        return ConversationHandler.END

    await query.message.reply_html(QUESTIONS[S.EMPLOYER_NAME])
    return S.EMPLOYER_NAME


async def on_resume_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or context.user_data is None:
        return ConversationHandler.END
    await query.answer()

    pending = context.user_data.pop(f"{SCENARIO}_pending_resume", None)
    if pending is None:
        # Чернетки нема (видалили в іншому місці) — починаємо з нуля.
        return await _begin_fresh(query, context)

    saved_state, saved_data = pending
    context.user_data[SCENARIO] = saved_data

    if update.effective_user is not None:
        funnel_emit("salary_resumed", telegram_id=update.effective_user.id, state=int(saved_state))
    await query.edit_message_text(f"▶️ Продовжуємо: {_progress_label(saved_state)}.")

    if saved_state == S.PREVIEW:
        await _send_preview(update, context)
        return S.PREVIEW

    if saved_state == S.PLAN_CHOICE:
        await _send_plan_choice(update, context)
        return S.PLAN_CHOICE

    question = QUESTIONS.get(saved_state)
    if question is None:
        return await _begin_fresh(query, context)

    if query.message is not None:
        await query.message.reply_html(question)
    return saved_state


async def on_resume_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or context.user_data is None or update.effective_user is None:
        return ConversationHandler.END
    await query.answer()

    delete_draft(update.effective_user.id, SCENARIO)
    funnel_emit("salary_restarted", telegram_id=update.effective_user.id)
    return await _begin_fresh(query, context)


# ============================================================================
# State handlers
# ============================================================================


async def on_employer_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.EMPLOYER_NAME
    try:
        value = validate_text(update.message.text, min_len=3, max_len=200, label="Назва роботодавця")
    except ValidationError as e:
        await _send_error(update, e)
        return S.EMPLOYER_NAME
    _data(context)["employer_name"] = value
    return await _advance_or_preview(update, context, S.EMPLOYER_EDRPOU, completed_field="employer_name")


async def on_employer_edrpou(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.EMPLOYER_EDRPOU
    try:
        value = validate_edrpou(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.EMPLOYER_EDRPOU
    _data(context)["employer_edrpou"] = value
    return await _advance_or_preview(update, context, S.EMPLOYER_ADDRESS, completed_field="employer_edrpou")


async def on_employer_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.EMPLOYER_ADDRESS
    try:
        value = validate_text(update.message.text, min_len=10, max_len=300, label="Адреса")
    except ValidationError as e:
        await _send_error(update, e)
        return S.EMPLOYER_ADDRESS
    _data(context)["employer_address"] = value
    return await _advance_or_preview(update, context, S.AMOUNT, completed_field="employer_address")


async def on_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.AMOUNT
    try:
        amount = validate_amount_uah(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.AMOUNT
    _data(context)["amount"] = amount
    return await _advance_or_preview(update, context, S.PERIOD_FROM, completed_field="amount")


async def on_period_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.PERIOD_FROM
    try:
        month, year = validate_month_year(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.PERIOD_FROM
    _data(context)["period_from"] = (month, year)
    return await _advance_or_preview(update, context, S.PERIOD_TO, completed_field="period_from")


async def on_period_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.PERIOD_TO
    try:
        month, year = validate_month_year(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.PERIOD_TO
    data = _data(context)
    data["period_to"] = (month, year)

    period_from = data["period_from"]
    if (year, month) < (period_from[1], period_from[0]):
        await update.message.reply_text(
            "⚠️ Кінцевий місяць не може бути раніше початкового. Введи ще раз."
        )
        return S.PERIOD_TO

    return await _advance_or_preview(update, context, S.LAST_PAYMENT_DATE, completed_field="period_to")


async def on_last_payment_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.LAST_PAYMENT_DATE
    try:
        d = validate_date(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.LAST_PAYMENT_DATE
    _data(context)["last_payment_date"] = d
    return await _advance_or_preview(update, context, S.USER_NAME, completed_field="last_payment_date")


async def on_user_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.USER_NAME
    try:
        value = validate_text(update.message.text, min_len=5, max_len=150, label="ПІБ")
    except ValidationError as e:
        await _send_error(update, e)
        return S.USER_NAME
    _data(context)["user_name"] = value
    return await _advance_or_preview(update, context, S.USER_TAX_ID, completed_field="user_name")


async def on_user_tax_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.USER_TAX_ID
    try:
        value = validate_tax_id(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.USER_TAX_ID
    _data(context)["user_tax_id"] = value
    return await _advance_or_preview(update, context, S.USER_ADDRESS, completed_field="user_tax_id")


async def on_user_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.USER_ADDRESS
    try:
        value = validate_text(update.message.text, min_len=10, max_len=300, label="Адреса")
    except ValidationError as e:
        await _send_error(update, e)
        return S.USER_ADDRESS
    _data(context)["user_address"] = value
    return await _advance_or_preview(update, context, S.USER_PHONE, completed_field="user_address")


async def on_user_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.USER_PHONE
    try:
        value = validate_phone(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.USER_PHONE
    _data(context)["user_phone"] = value
    return await _advance_or_preview(update, context, S.PREVIEW, completed_field="user_phone")


# ============================================================================
# План вирішення — три варіанти
# ============================================================================


async def _send_plan_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = update.message
    if target is None and update.callback_query is not None:
        target = update.callback_query.message
    if target is None:
        return
    data = _data(context)
    summary = (
        "<b>✅ Дані зібрано.</b>\n\n"
        f"• Роботодавець: {data['employer_name']}\n"
        f"• Сума: {format_amount_uah(data['amount'])}\n"
        f"• Період: {format_month_year(*data['period_from'])} — "
        f"{format_month_year(*data['period_to'])}\n\n"
        "<b>Обери шлях вирішення:</b>\n\n"
        "🟢 <b>Мʼякий — претензія роботодавцю</b>\n"
        "Перший крок. Даємо роботодавцю шанс виплатити добровільно. "
        "Швидко, без судів. Закон дає 7 днів на відповідь.\n\n"
        "🟡 <b>Контролюючий орган — Держпраці</b>\n"
        "Скарга в державну службу з питань праці. Вони проводять перевірку, "
        "штрафують роботодавця. Може мотивувати до швидкої виплати.\n\n"
        "🔴 <b>Судовий — позов до суду</b>\n"
        "Найдовший, але найдієвіший шлях. Через суд + виконавчу службу. "
        "Судовий збір не сплачуєш (звільнений за законом).\n\n"
        "📦 <b>Усі три</b> — отримаєш одразу 3 документи. Можна подавати паралельно.\n"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🟢 Претензія роботодавцю", callback_data="salary_plan:employer")],
            [InlineKeyboardButton("🟡 Скарга в Держпраці", callback_data="salary_plan:labor_office")],
            [InlineKeyboardButton("🔴 Позов до суду", callback_data="salary_plan:court")],
            [InlineKeyboardButton("📦 Усі три документи", callback_data="salary_plan:all")],
        ]
    )
    await target.reply_html(summary, reply_markup=keyboard)


# ============================================================================
# Прев'ю + редагування окремого поля
# ============================================================================


async def on_preview_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.message is None:
        return ConversationHandler.END
    await query.answer()

    if update.effective_user is not None:
        funnel_emit("salary_preview_confirmed", telegram_id=update.effective_user.id)

    text = (
        "⚠️ <b>Важливо:</b> у подібних справах можуть виникати нюанси, які впливають "
        "на результат (позиція другої сторони, докази, виконання рішення суду тощо).\n\n"
        "<b>Оберіть, як діяти далі 👇</b>"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 Отримати шаблон документа", callback_data="pregen:template")],
            [InlineKeyboardButton("👩‍⚖️ Розібрати ситуацію з юристом", callback_data="pregen:lawyer")],
        ]
    )
    await query.message.reply_html(text, reply_markup=keyboard)
    _persist(update, context, S.PRE_GENERATE)
    return S.PRE_GENERATE


async def on_pregen_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()
    if update.effective_user is not None:
        funnel_emit("salary_pregen_template", telegram_id=update.effective_user.id)
    await _send_plan_choice(update, context)
    _persist(update, context, S.PLAN_CHOICE)
    return S.PLAN_CHOICE


async def on_pregen_lawyer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Користувач хоче до юриста. Вийти з salary FSM і пустити форму з полем 'labor'."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return ConversationHandler.END
    await query.answer()

    funnel_emit("salary_pregen_lawyer", telegram_id=update.effective_user.id)

    # Чистимо salary state — більше не повертаємось до зарплати, юзер обрав консультацію.
    if context.user_data is not None:
        context.user_data.pop(SCENARIO, None)
        context.user_data.pop(EDITING_KEY, None)
    delete_draft(update.effective_user.id, SCENARIO)

    # Імітуємо вхід у consultation FSM. Робимо це через CallbackQuery з даними "consult_start:labor"
    # — простіше показати повідомлення з кнопкою, яку юзер натискає сам:
    if query.message is not None:
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📩 Записатися — Трудове право",
                                      callback_data="consult_start:labor")],
                [InlineKeyboardButton("🔙 До головного меню", callback_data="main:home")],
            ]
        )
        await query.message.reply_html(
            "👩‍⚖️ Ваші дані з анкети не передаються — заявка для юриста зберігає тільки те, "
            "що ви введете нижче. Натисніть, щоб запустити форму запису.",
            reply_markup=keyboard,
        )
    return ConversationHandler.END


async def on_preview_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.message is None:
        return ConversationHandler.END
    await query.answer()

    rows = []
    for key, label, _state in FIELDS:
        rows.append([InlineKeyboardButton(f"✏️ {label}", callback_data=f"edit_field:{key}")])
    rows.append([InlineKeyboardButton("⬅️ Назад до прев'ю", callback_data="edit_field:__back__")])

    await query.message.reply_html(
        "Яке поле виправляємо?", reply_markup=InlineKeyboardMarkup(rows)
    )
    return S.EDIT_FIELD_CHOICE


async def on_preview_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return ConversationHandler.END
    await query.answer()
    funnel_emit("salary_cancelled", telegram_id=update.effective_user.id, source="preview")
    delete_draft(update.effective_user.id, SCENARIO)
    if context.user_data is not None:
        context.user_data.pop(SCENARIO, None)
        context.user_data.pop(EDITING_KEY, None)
    await query.edit_message_text(
        "Ок, скасовано. Чернетку видалено. Натисни /menu щоб обрати інший сценарій."
    )
    return ConversationHandler.END


async def on_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return S.EDIT_FIELD_CHOICE
    await query.answer()

    field_key = query.data.split(":", 1)[1]

    if field_key == "__back__":
        await _send_preview(update, context)
        return S.PREVIEW

    target_state = next((state for key, _, state in FIELDS if key == field_key), None)
    if target_state is None or target_state not in QUESTIONS:
        return S.EDIT_FIELD_CHOICE

    context.user_data[EDITING_KEY] = field_key
    if query.message is not None:
        await query.message.reply_html(QUESTIONS[target_state])
    return target_state


def _build_template_context(data: dict[str, Any]) -> dict[str, Any]:
    period_from_text = format_month_year(*data["period_from"])
    period_to_text = format_month_year(*data["period_to"])
    return {
        "today": now_date_str(),
        "employer_name": data["employer_name"],
        "employer_edrpou_or_dash": data.get("employer_edrpou") or "—",
        "employer_address": data["employer_address"],
        "amount_text": format_amount_uah(data["amount"]),
        "period_from_text": period_from_text,
        "period_to_text": period_to_text,
        "last_payment_date": format_date(data["last_payment_date"]),
        "user_name": data["user_name"],
        "user_tax_id": data["user_tax_id"],
        "user_address": data["user_address"],
        "user_phone": data["user_phone"],
        "court_name_or_placeholder": "_______________________ районного суду",
    }


PLAN_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "employer": [("salary_claim_employer.docx", "Претензія роботодавцю")],
    "labor_office": [("salary_claim_labor_office.docx", "Скарга до Держпраці")],
    "court": [("salary_court_claim.docx", "Позовна заява до суду")],
    "all": [
        ("salary_claim_employer.docx", "Претензія роботодавцю"),
        ("salary_claim_labor_office.docx", "Скарга до Держпраці"),
        ("salary_court_claim.docx", "Позовна заява до суду"),
    ],
}

CHECKLISTS: dict[str, str] = {
    "employer": (
        "<b>📋 Що робити з претензією роботодавцю</b>\n\n"
        "1. Роздрукуй у 2 примірниках.\n"
        "2. Підпиши, постав дату.\n"
        "3. Передай керівнику особисто (нехай поставить відмітку «отримано» на твоєму примірнику) "
        "або відправ <b>рекомендованим листом з описом вкладення</b> через Укрпошту на юр. адресу.\n"
        "4. Очікуй відповіді 7 календарних днів.\n"
        "5. Якщо не реагують — подавай скаргу в Держпраці і/або позов до суду."
    ),
    "labor_office": (
        "<b>📋 Що робити зі скаргою до Держпраці</b>\n\n"
        "1. Знайди адресу територіального управління Держпраці за областю реєстрації роботодавця "
        "на dsp.gov.ua.\n"
        "2. Підпиши заяву.\n"
        "3. Подай: особисто в канцелярії / рекомендованим листом / через електронний кабінет на dsp.gov.ua.\n"
        "4. Збережи копію з відміткою прийому або поштовий трек.\n"
        "5. Розгляд — до 30 днів."
    ),
    "court": (
        "<b>📋 Що робити з позовом до суду</b>\n\n"
        "1. Підпиши заяву.\n"
        "2. Підготуй додатки (перелічені в кінці позову): копія паспорта, ІПН, трудового договору, "
        "розрахунок заборгованості. Зроби копії і для відповідача.\n"
        "3. Подай у канцелярію районного суду <b>за місцем реєстрації роботодавця або своїм</b>.\n"
        "4. Судовий збір НЕ сплачуєш (звільнений за п. 1 ч. 1 ст. 5 ЗУ «Про судовий збір»).\n"
        "5. Очікуй ухвалу про відкриття провадження — ~10–14 днів.\n"
        "6. Після рішення — виконавчий лист → державна виконавча служба для стягнення."
    ),
}


async def on_plan_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return ConversationHandler.END
    await query.answer()

    plan = query.data.split(":", 1)[1]
    if plan not in PLAN_TEMPLATES:
        return ConversationHandler.END

    funnel_emit("salary_plan_chosen", telegram_id=update.effective_user.id, plan=plan)

    data = _data(context)
    tpl_context = _build_template_context(data)

    await query.edit_message_text(
        f"⏳ Генерую документ(и)…\n\nПлан: <b>{_plan_label(plan)}</b>",
        parse_mode="HTML",
    )

    if query.message is None:
        return ConversationHandler.END

    telegram_id = update.effective_user.id
    docs_count = 0
    for template_name, title in PLAN_TEMPLATES[plan]:
        try:
            output_path = render(template_name, tpl_context, telegram_id=telegram_id)
        except Exception:
            log.exception("render_failed", template=template_name)
            await query.message.reply_text(
                f"❌ Помилка генерації {title}. Адміни вже сповіщені."
            )
            continue

        with output_path.open("rb") as f:
            await query.message.reply_document(
                document=f,
                filename=output_path.name,
                caption=f"📄 <b>{title}</b>",
                parse_mode="HTML",
            )
        docs_count += 1

    if plan == "all":
        for key in ("employer", "labor_office", "court"):
            await query.message.reply_html(CHECKLISTS[key])
    else:
        await query.message.reply_html(CHECKLISTS[plan])

    await _send_post_generation_card(update, context)
    _record_completion(update, plan=plan, docs_count=docs_count)
    funnel_emit(
        "salary_completed",
        telegram_id=update.effective_user.id,
        plan=plan,
        docs_count=docs_count,
    )

    delete_draft(update.effective_user.id, SCENARIO)
    context.user_data.pop(SCENARIO, None)
    return ConversationHandler.END


def _plan_label(plan: str) -> str:
    return {
        "employer": "Претензія роботодавцю",
        "labor_office": "Скарга до Держпраці",
        "court": "Позов до суду",
        "all": "Усі три документи",
    }.get(plan, plan)


async def _send_post_generation_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.callback_query.message if update.callback_query else update.message
    if msg is None:
        return

    text = (
        "📄 <b>Документ сформовано</b>\n\n"
        "⚠️ Зверніть увагу: документ має шаблонний характер і не враховує всіх "
        "індивідуальних обставин. У більшості випадків для досягнення результату "
        "потрібна адаптація документа під конкретну ситуацію. Ви можете втратити час "
        "або кошти, якщо діяти без урахування усіх обставин."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👩‍⚖️ Перевірити документ юристом", callback_data="post:lawyer_review")],
            [InlineKeyboardButton("📩 Отримати консультацію", callback_data="main:consult")],
            [InlineKeyboardButton("✍️ Відредагувати документ", callback_data="post:edit_hint")],
            [InlineKeyboardButton("🔙 До головного меню", callback_data="main:home")],
        ]
    )
    await msg.reply_html(text, reply_markup=keyboard)


async def on_post_lawyer_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Аналог 'Отримати консультацію' для post-generation — пізніше можна різнити тексти."""
    from pravohelp.handlers.start import on_main_consult
    await on_main_consult(update, context)


async def on_post_edit_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    await query.answer()
    await query.message.reply_html(
        "✍️ <b>Як відредагувати документ</b>\n\n"
        "1. Завантаж DOCX-файл, який щойно надіслав бот.\n"
        "2. Відкрий його у Microsoft Word, LibreOffice Writer або Google Docs.\n"
        "3. Виправ потрібні поля під свою ситуацію (дати, цифри, формулювання).\n"
        "4. Збережи і використовуй за призначенням.\n\n"
        "💡 Якщо не впевнений, що саме потрібно міняти — краще звернутись за консультацією."
    )


def _record_completion(update: Update, *, plan: str, docs_count: int) -> None:
    if update.effective_user is None:
        return
    with get_session() as session:
        user = session.query(User).filter_by(telegram_id=update.effective_user.id).one_or_none()
        if user is None:
            return
        req = ScenarioRequest(
            user_id=user.id,
            scenario=SCENARIO,
            status="completed",
            plan_chosen=plan,
            documents_generated=docs_count,
            completed_at=datetime.now(UTC),
        )
        session.add(req)


# ============================================================================
# Cancel
# ============================================================================


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data is not None:
        context.user_data.pop(SCENARIO, None)
    if update.effective_user is not None:
        funnel_emit(
            "salary_cancelled", telegram_id=update.effective_user.id, source="cmd_cancel"
        )
        delete_draft(update.effective_user.id, SCENARIO)
    if update.message is not None:
        await update.message.reply_text(
            "Ок, вийшли зі сценарію. Чернетку видалено. Натисни /menu щоб обрати інший."
        )
    return ConversationHandler.END


# ============================================================================
# Conversation registration
# ============================================================================


def build_salary_conversation() -> ConversationHandler:
    text_filter = filters.TEXT & ~filters.COMMAND

    # per_message=False — стани змішують MessageHandler і CallbackQueryHandler,
    # тому per_message=True використовувати не можна. Явно проставлено, щоб
    # глушити PTBUserWarning і зафіксувати свідомий вибір.
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_salary, pattern=r"^scenario:salary$")],
        states={
            S.RESUME_PROMPT: [
                CallbackQueryHandler(on_resume_yes, pattern=r"^salary_resume:yes$"),
                CallbackQueryHandler(on_resume_no, pattern=r"^salary_resume:no$"),
            ],
            S.EMPLOYER_NAME: [MessageHandler(text_filter, on_employer_name)],
            S.EMPLOYER_EDRPOU: [MessageHandler(text_filter, on_employer_edrpou)],
            S.EMPLOYER_ADDRESS: [MessageHandler(text_filter, on_employer_address)],
            S.AMOUNT: [MessageHandler(text_filter, on_amount)],
            S.PERIOD_FROM: [MessageHandler(text_filter, on_period_from)],
            S.PERIOD_TO: [MessageHandler(text_filter, on_period_to)],
            S.LAST_PAYMENT_DATE: [MessageHandler(text_filter, on_last_payment_date)],
            S.USER_NAME: [MessageHandler(text_filter, on_user_name)],
            S.USER_TAX_ID: [MessageHandler(text_filter, on_user_tax_id)],
            S.USER_ADDRESS: [MessageHandler(text_filter, on_user_address)],
            S.USER_PHONE: [MessageHandler(text_filter, on_user_phone)],
            S.PREVIEW: [
                CallbackQueryHandler(on_preview_confirm, pattern=r"^preview:confirm$"),
                CallbackQueryHandler(on_preview_edit, pattern=r"^preview:edit$"),
                CallbackQueryHandler(on_preview_cancel, pattern=r"^preview:cancel$"),
            ],
            S.EDIT_FIELD_CHOICE: [
                CallbackQueryHandler(on_edit_field, pattern=r"^edit_field:"),
            ],
            S.PRE_GENERATE: [
                CallbackQueryHandler(on_pregen_template, pattern=r"^pregen:template$"),
                CallbackQueryHandler(on_pregen_lawyer, pattern=r"^pregen:lawyer$"),
            ],
            S.PLAN_CHOICE: [CallbackQueryHandler(on_plan_choice, pattern=r"^salary_plan:")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="salary_scenario",
        persistent=False,
        per_message=False,
    )

"""ConversationHandler для сценарію «невиплата зарплати»."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
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


SCENARIO = "salary"


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
    context.user_data[SCENARIO] = {"started_at": datetime.now(timezone.utc)}
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

    if saved_state == S.PLAN_CHOICE:
        await query.edit_message_text("▶️ Продовжуємо. Усі дані вже зібрано.")
        if query.message is not None:
            # Симулюємо update.message через query.message для _send_plan_choice
            class _Shim:
                message = query.message
            await _send_plan_choice(_Shim(), context)  # type: ignore[arg-type]
        return S.PLAN_CHOICE

    question = QUESTIONS.get(saved_state)
    if question is None:
        return await _begin_fresh(query, context)

    await query.edit_message_text(f"▶️ Продовжуємо з кроку {_progress_label(saved_state)}.")
    if query.message is not None:
        await query.message.reply_html(question)
    return saved_state


async def on_resume_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or context.user_data is None or update.effective_user is None:
        return ConversationHandler.END
    await query.answer()

    delete_draft(update.effective_user.id, SCENARIO)
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
    _persist(update, context, S.EMPLOYER_EDRPOU)

    await _send_question(update, QUESTIONS[S.EMPLOYER_EDRPOU])
    return S.EMPLOYER_EDRPOU


async def on_employer_edrpou(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.EMPLOYER_EDRPOU
    try:
        value = validate_edrpou(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.EMPLOYER_EDRPOU
    _data(context)["employer_edrpou"] = value
    _persist(update, context, S.EMPLOYER_ADDRESS)

    await _send_question(update, QUESTIONS[S.EMPLOYER_ADDRESS])
    return S.EMPLOYER_ADDRESS


async def on_employer_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.EMPLOYER_ADDRESS
    try:
        value = validate_text(update.message.text, min_len=10, max_len=300, label="Адреса")
    except ValidationError as e:
        await _send_error(update, e)
        return S.EMPLOYER_ADDRESS
    _data(context)["employer_address"] = value
    _persist(update, context, S.AMOUNT)

    await _send_question(update, QUESTIONS[S.AMOUNT])
    return S.AMOUNT


async def on_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.AMOUNT
    try:
        amount = validate_amount_uah(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.AMOUNT
    _data(context)["amount"] = amount
    _persist(update, context, S.PERIOD_FROM)

    await _send_question(update, QUESTIONS[S.PERIOD_FROM])
    return S.PERIOD_FROM


async def on_period_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.PERIOD_FROM
    try:
        month, year = validate_month_year(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.PERIOD_FROM
    _data(context)["period_from"] = (month, year)
    _persist(update, context, S.PERIOD_TO)

    await _send_question(update, QUESTIONS[S.PERIOD_TO])
    return S.PERIOD_TO


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

    _persist(update, context, S.LAST_PAYMENT_DATE)

    await _send_question(update, QUESTIONS[S.LAST_PAYMENT_DATE])
    return S.LAST_PAYMENT_DATE


async def on_last_payment_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.LAST_PAYMENT_DATE
    try:
        d = validate_date(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.LAST_PAYMENT_DATE
    _data(context)["last_payment_date"] = d
    _persist(update, context, S.USER_NAME)

    await _send_question(update, QUESTIONS[S.USER_NAME])
    return S.USER_NAME


async def on_user_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.USER_NAME
    try:
        value = validate_text(update.message.text, min_len=5, max_len=150, label="ПІБ")
    except ValidationError as e:
        await _send_error(update, e)
        return S.USER_NAME
    _data(context)["user_name"] = value
    _persist(update, context, S.USER_TAX_ID)

    await _send_question(update, QUESTIONS[S.USER_TAX_ID])
    return S.USER_TAX_ID


async def on_user_tax_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.USER_TAX_ID
    try:
        value = validate_tax_id(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.USER_TAX_ID
    _data(context)["user_tax_id"] = value
    _persist(update, context, S.USER_ADDRESS)

    await _send_question(update, QUESTIONS[S.USER_ADDRESS])
    return S.USER_ADDRESS


async def on_user_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.USER_ADDRESS
    try:
        value = validate_text(update.message.text, min_len=10, max_len=300, label="Адреса")
    except ValidationError as e:
        await _send_error(update, e)
        return S.USER_ADDRESS
    _data(context)["user_address"] = value
    _persist(update, context, S.USER_PHONE)

    await _send_question(update, QUESTIONS[S.USER_PHONE])
    return S.USER_PHONE


async def on_user_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return S.USER_PHONE
    try:
        value = validate_phone(update.message.text)
    except ValidationError as e:
        await _send_error(update, e)
        return S.USER_PHONE
    _data(context)["user_phone"] = value
    _persist(update, context, S.PLAN_CHOICE)

    await _send_plan_choice(update, context)
    return S.PLAN_CHOICE


# ============================================================================
# План вирішення — три варіанти
# ============================================================================


async def _send_plan_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
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
    await update.message.reply_html(summary, reply_markup=keyboard)


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

    await _send_lawyer_offer(update, context)
    _record_completion(update, plan=plan, docs_count=docs_count)

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


async def _send_lawyer_offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = load_settings()
    msg = update.callback_query.message if update.callback_query else update.message
    if msg is None:
        return

    contact_lines = []
    if settings.lawyer_telegram:
        contact_lines.append(f"Telegram: {settings.lawyer_telegram}")
    if settings.lawyer_phone:
        contact_lines.append(f"Телефон: {settings.lawyer_phone}")
    contact_block = "\n".join(contact_lines) if contact_lines else "(контакти зʼявляться скоро)"

    text = (
        "💼 <b>Хочеш, щоб документ і твою ситуацію перевірив юрист?</b>\n\n"
        f"Можемо звʼязати з <b>{settings.lawyer_name}</b> — практикуючий юрист.\n\n"
        f"{contact_block}\n\n"
        "Юрист допоможе адаптувати шаблон під твою конкретну ситуацію, "
        "перевірити підстави і супроводжувати справу далі."
    )
    await msg.reply_html(text)


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
            completed_at=datetime.now(timezone.utc),
        )
        session.add(req)


# ============================================================================
# Cancel
# ============================================================================


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data is not None:
        context.user_data.pop(SCENARIO, None)
    if update.effective_user is not None:
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
            S.PLAN_CHOICE: [CallbackQueryHandler(on_plan_choice, pattern=r"^salary_plan:")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="salary_scenario",
        persistent=False,
        per_message=False,
    )

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
from pravohelp.storage.models import ScenarioRequest, User
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


SCENARIO = "salary"


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


# ============================================================================
# Entry point — викликається з callback "scenario:salary"
# ============================================================================


async def start_salary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    if context.user_data is None:
        return ConversationHandler.END

    context.user_data[SCENARIO] = {"started_at": datetime.now(timezone.utc)}

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

    await query.message.reply_html(
        "<b>1/11.</b> Як називається організація-роботодавець?\n\n"
        "Введи точну назву так, як вона у трудовому договорі. "
        "Наприклад: <i>ТОВ «Промінь»</i> або <i>ФОП Іваненко Іван Іванович</i>."
    )
    return S.EMPLOYER_NAME


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

    await _send_question(
        update,
        "<b>2/11.</b> ЄДРПОУ роботодавця (8 цифр).\n\n"
        "Знайдеш у трудовому договорі або на сайті <a href='https://usr.minjust.gov.ua/'>"
        "Єдиного держреєстру</a>.\n\n"
        "Якщо не знаєш — напиши <b>не знаю</b>.",
    )
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

    await _send_question(
        update,
        "<b>3/11.</b> Юридична адреса роботодавця.\n\n"
        "Як вона у договорі або в ЄДР. Наприклад: <i>04050, м. Київ, вул. Січових Стрільців, 50, оф. 12</i>.",
    )
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

    await _send_question(
        update,
        "<b>4/11.</b> Сума заборгованості (у гривнях).\n\n"
        "Вкажи число — наприклад <code>15000</code> або <code>23500.50</code>.",
    )
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

    await _send_question(
        update,
        "<b>5/11.</b> З якого місяця почалась заборгованість?\n\n"
        "Формат: <code>ММ.РРРР</code>. Наприклад: <code>01.2026</code> (січень 2026).",
    )
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

    await _send_question(
        update,
        "<b>6/11.</b> По який місяць триває заборгованість?\n\n"
        "Формат: <code>ММ.РРРР</code>. Якщо лише один місяць — повтори той самий, що в попередньому питанні.",
    )
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

    await _send_question(
        update,
        "<b>7/11.</b> Дата останньої виплати зарплати.\n\n"
        "Формат: <code>ДД.ММ.РРРР</code>. Наприклад: <code>15.12.2025</code>.\n"
        "Якщо ніколи не отримував — введи дату початку роботи.",
    )
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

    await _send_question(
        update,
        "<b>8/11.</b> Твоє ПІБ повністю.\n\n"
        "Як у паспорті. Наприклад: <i>Іваненко Олександр Сергійович</i>.",
    )
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

    await _send_question(
        update,
        "<b>9/11.</b> Твій ІПН (РНОКПП) — 10 цифр.\n\n"
        "Без пробілів і дефісів.",
    )
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

    await _send_question(
        update,
        "<b>10/11.</b> Твоя адреса для листування.\n\n"
        "Куди має прийти відповідь. Наприклад: "
        "<i>03150, м. Київ, вул. Велика Васильківська, 100, кв. 25</i>.",
    )
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

    await _send_question(
        update,
        "<b>11/11.</b> Твій телефон.\n\n"
        "У форматі <code>+380XXXXXXXXX</code> або <code>0XXXXXXXXX</code>.",
    )
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
    if update.message is not None:
        await update.message.reply_text(
            "Ок, вийшли зі сценарію. Натисни /start щоб обрати інший."
        )
    return ConversationHandler.END


# ============================================================================
# Conversation registration
# ============================================================================


def build_salary_conversation() -> ConversationHandler:
    text_filter = filters.TEXT & ~filters.COMMAND

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_salary, pattern=r"^scenario:salary$")],
        states={
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
    )

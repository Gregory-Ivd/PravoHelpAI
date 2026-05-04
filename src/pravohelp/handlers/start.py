from __future__ import annotations

from datetime import datetime, timezone

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from pravohelp.storage.db import get_session
from pravohelp.storage.models import User

log = structlog.get_logger(__name__)


MONOBANK_URL = "https://send.monobank.ua/jar/23mRV6qChb"
RADA_URL = "https://www.rada.gov.ua/"
LAWYER_TG_USERNAME = "dmytrolawua"  # без @, для t.me/<username>


DISCLAIMER = (
    "⚖️ <b>PravoHelpAI — генератор юридичних шаблонів</b>\n\n"
    "Бот допомагає підготувати документи для типових ситуацій і "
    "<b>не є заміною індивідуальної юридичної консультації</b>.\n\n"
    "<b>Важливо:</b> Згенерований документ має шаблонний характер і "
    "потребує адаптації під конкретну ситуацію користувача. Перед поданням "
    "до державних органів або суду рекомендується перевірка юристом. "
    "Використання документа здійснюється користувачем на власний ризик. "
    "Розробник не несе відповідальності за наслідки використання згенерованих "
    "матеріалів. Законодавство України, зокрема у сфері військового обовʼязку "
    "та мобілізації, може змінюватися — актуальність інформації рекомендується "
    f'перевіряти на сайті <a href="{RADA_URL}">Верховної Ради України</a>.\n\n'
    "<b>Підтвердження:</b> Натискаючи «Згоден», ви підтверджуєте, що "
    "ознайомилися з умовами та приймаєте їх."
)


MAIN_MENU_TEXT = "Оберіть, що вам потрібно 👇"

SCENARIOS_MENU_TEXT = (
    "Я допоможу підготувати документ для типової ситуації.\n\n"
    "<b>Оберіть тип документа 👇</b>"
)

CONSULT_INTRO_TEXT = (
    "👩‍⚖️ <b>Консультація юриста</b>\n\n"
    "Ви можете отримати первинну юридичну консультацію з аналізом вашої ситуації "
    "та чіткими рекомендаціями щодо подальших дій у найкоротші строки.\n\n"
    "Оперативний аналіз дозволяє своєчасно врахувати важливі обставини та обрати "
    "правильний напрям дій, мінімізуючи ризик помилок і втрати часу на початковому "
    "етапі. Це допоможе оцінити перспективи справи та визначити оптимальну стратегію "
    "дій з урахуванням ваших інтересів.\n\n"
    "<b>🔹 Умови</b>\n"
    "Первинна консультація передбачає оцінку ситуації. "
    "Детальний аналіз, підготовка документів та супровід здійснюються на платній основі.\n\n"
    "<b>Оберіть галузь права 👇</b>"
)


CONSULT_FIELDS: list[tuple[str, str, str, str]] = [
    (
        "family",
        "⚖️ Сімейне право",
        "⚖️ <b>Сімейне право</b>",
        "Розірвання шлюбу, поділ майна, аліменти та пеня, визначення місця проживання "
        "дитини, графік спілкування, позбавлення батьківських прав, виїзд дітей за кордон.",
    ),
    (
        "labor",
        "💼 Трудове право",
        "💼 <b>Трудове право</b>",
        "Заборгованість по заробітній платі, трудові спори з роботодавцем, "
        "підготовка звернень та позовів.",
    ),
    (
        "military",
        "🪖 Військове право",
        "🪖 <b>Військове право</b>",
        "Консультації щодо мобілізації, повісток, супровід у типових ситуаціях.",
    ),
    (
        "medical",
        "🏥 Медичне та соціальне право",
        "🏥 <b>Медичне та соціальне право</b>",
        "Спори з медичними установами, захист прав пацієнтів, оскарження рішень "
        "щодо встановлення групи інвалідності, супровід у МСЕК.",
    ),
    (
        "tax",
        "💰 Податкове право",
        "💰 <b>Податкове право</b>",
        "Консультації щодо податкових зобовʼязань та спорів.",
    ),
    (
        "inheritance",
        "🏠 Спадкове право",
        "🏠 <b>Спадкове право</b>",
        "Оформлення спадщини, вирішення спорів між спадкоємцями.",
    ),
    (
        "international",
        "🌍 Міжнародні питання",
        "🌍 <b>Міжнародні та документи за кордоном</b>",
        "Підтвердження документів за кордоном, складання заяв та скарг "
        "(у тому числі в Польщі), правові питання між Україною та іншими країнами.",
    ),
    (
        "admin",
        "🚗 Адміністративне (ПДР)",
        "🚗 <b>Адміністративне право (ПДР)</b>",
        "Штрафи, оскарження постанов, підготовка заяв та скарг.",
    ),
    (
        "other",
        "📄 Інші питання",
        "📄 <b>Інші питання</b>",
        "Відновлення документів, підготовка індивідуальних заяв та звернень.",
    ),
]


# ============================================================================
# Клавіатури
# ============================================================================


def _btn_consult() -> InlineKeyboardButton:
    return InlineKeyboardButton("👩‍⚖️ Консультація юриста", callback_data="main:consult")


def _btn_donate() -> InlineKeyboardButton:
    return InlineKeyboardButton("☕️ Підтримати канал", url=MONOBANK_URL)


def _btn_back_main() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 До головного меню", callback_data="main:home")


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 Скласти документ", callback_data="main:scenarios")],
            [_btn_consult()],
            [_btn_donate()],
        ]
    )


def scenarios_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 Невиплата зарплати", callback_data="scenario:salary")],
            [_btn_consult()],
            [_btn_back_main()],
        ]
    )


def consult_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"consult_field:{key}")]
            for key, label, _, _ in CONSULT_FIELDS]
    rows.append([_btn_back_main()])
    return InlineKeyboardMarkup(rows)


def consult_field_keyboard(field_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📩 Написати юристу", url=f"https://t.me/{LAWYER_TG_USERNAME}")],
            [InlineKeyboardButton("📅 Записатися на консультацію",
                                  callback_data=f"consult_start:{field_key}")],
            [InlineKeyboardButton("🔙 До галузей", callback_data="main:consult")],
            [_btn_back_main()],
        ]
    )


# ============================================================================
# Команди
# ============================================================================


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
        await update.message.reply_html(
            MAIN_MENU_TEXT, reply_markup=main_menu_keyboard()
        )
        return

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Згоден", callback_data="disclaimer:accept")],
            [InlineKeyboardButton("❌ Не згоден", callback_data="disclaimer:decline")],
        ]
    )
    await update.message.reply_html(
        DISCLAIMER, reply_markup=keyboard, disable_web_page_preview=True
    )


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

    await update.message.reply_html(MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())


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
        MAIN_MENU_TEXT, parse_mode="HTML", reply_markup=main_menu_keyboard()
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


# ============================================================================
# Навігація між меню
# ============================================================================


async def on_main_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text(
        MAIN_MENU_TEXT, parse_mode="HTML", reply_markup=main_menu_keyboard()
    )


async def on_main_scenarios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text(
        SCENARIOS_MENU_TEXT, parse_mode="HTML", reply_markup=scenarios_menu_keyboard()
    )


async def on_main_consult(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    # edit_message_text — якщо джерело edit-able (інлайн-callback). Інакше — нове повідомлення.
    try:
        await query.edit_message_text(
            CONSULT_INTRO_TEXT, parse_mode="HTML", reply_markup=consult_menu_keyboard()
        )
    except Exception:
        if query.message is not None:
            await query.message.reply_html(
                CONSULT_INTRO_TEXT, reply_markup=consult_menu_keyboard()
            )


async def on_consult_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    field_key = query.data.split(":", 1)[1]
    entry = next((e for e in CONSULT_FIELDS if e[0] == field_key), None)
    if entry is None:
        return
    _, _, title, description = entry

    text = f"{title}\n\n{description}"
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=consult_field_keyboard(field_key)
        )
    except Exception:
        if query.message is not None:
            await query.message.reply_html(
                text, reply_markup=consult_field_keyboard(field_key)
            )


# ============================================================================
# Інші
# ============================================================================


async def cmd_cancel_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальний /cancel — спрацьовує, коли користувач не в ConversationHandler."""
    if update.message is None:
        return
    await update.message.reply_text(
        "Зараз ти не в сценарії — нема чого скасовувати. Натисни /menu щоб відкрити меню."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_html(
        "<b>Як користуватись ботом</b>\n\n"
        "1. /start — прийняти умови (один раз).\n"
        "2. /menu — головне меню.\n"
        "3. Обери «📄 Скласти документ» → тип документа → відповідай на питання.\n"
        "4. У кінці перевір дані, отримай DOCX і чек-лист.\n"
        "5. Опціонально — звернись за консультацією до юриста.\n\n"
        "<b>Команди</b>\n"
        "/start — перший запуск і дисклеймер\n"
        "/menu — головне меню\n"
        "/cancel — вийти з поточного сценарію\n"
        "/help — це повідомлення\n\n"
        "<b>FAQ</b>\n\n"
        "<b>Помилився у відповіді — як виправити?</b>\n"
        "Дій до кінця анкети — на екрані «Перевір дані» можна виправити будь-яке поле, "
        "не починаючи спочатку. Або /cancel і заново.\n\n"
        "<b>Чи зберігаються мої дані?</b>\n"
        "У БД назавжди — лише факт використання (telegram_id, обраний сценарій, дата). "
        "Поки сценарій не завершено — твої відповіді (ПІБ, ІПН, адреса, телефон) "
        "тимчасово зберігаються як <b>чернетка</b>, щоб ти міг продовжити, "
        "якщо щось перервалось. Чернетка автоматично видаляється:\n"
        "• після завершення сценарію або /cancel — одразу;\n"
        "• в інших випадках — через 24 години.\n"
        "Згенерований DOCX зникає з сервера через ~1 годину — встигни завантажити.\n\n"
        "<b>Документ — це готовий папір?</b>\n"
        "Ні, це шаблон. Перед поданням у держоргани/суд перевір під свою ситуацію — "
        "або сам, або з юристом. Бот не несе відповідальності за результат.\n\n"
        "<b>Як отримати консультацію юриста?</b>\n"
        "Натисни «👩‍⚖️ Консультація юриста» в головному меню — там вибір галузі права "
        "і контакти юриста-партнера.\n\n"
        "<b>Чи безпечно вводити персональні дані в Telegram?</b>\n"
        "Telegram шифрує переписку клієнт-сервер. Бот не передає твої дані третім сторонам. "
        "Якщо параноя — введи мінімум обовʼязкового і доповни вручну в DOCX."
    )

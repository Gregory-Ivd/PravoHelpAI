"""E2E прогін salary-сценарію через FSM з мокованими Telegram-обʼєктами."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from pravohelp.handlers import salary
from pravohelp.handlers.salary import S
from pravohelp.storage.db import init_db
from pravohelp.storage.models import User
from pravohelp.utils.rate_limit import reset_all


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "e2e.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    init_db(f"sqlite:///{db_file}")
    reset_all()

    # Створюємо юзера з прийнятим disclaimer (інакше cmd_start блокує)
    from datetime import datetime

    from pravohelp.storage.db import get_session

    with get_session() as s:
        s.add(User(telegram_id=42, disclaimer_accepted_at=datetime.now(UTC)))

    yield


def _make_message_update(text: str, *, telegram_id: int = 42):
    msg = MagicMock()
    msg.text = text
    msg.reply_html = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.reply_document = AsyncMock()
    user = MagicMock()
    user.id = telegram_id
    user.username = "tester"

    update = MagicMock()
    update.message = msg
    update.effective_user = user
    update.callback_query = None
    return update


def _make_callback_update(data: str, *, telegram_id: int = 42):
    msg = MagicMock()
    msg.reply_html = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.reply_document = AsyncMock()

    query = MagicMock()
    query.data = data
    query.message = msg
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    user = MagicMock()
    user.id = telegram_id

    update = MagicMock()
    update.message = None
    update.callback_query = query
    update.effective_user = user
    return update


def _make_context():
    ctx = MagicMock()
    ctx.user_data = {}
    return ctx


@pytest.mark.asyncio
async def test_full_salary_flow_through_preview(env):
    ctx = _make_context()

    # Entry: натискаємо кнопку «Невиплата зарплати»
    state = await salary.start_salary(_make_callback_update("scenario:salary"), ctx)
    assert state == S.EMPLOYER_NAME

    state = await salary.on_employer_name(_make_message_update("ТОВ Тест"), ctx)
    assert state == S.EMPLOYER_EDRPOU

    state = await salary.on_employer_edrpou(_make_message_update("12345678"), ctx)
    assert state == S.EMPLOYER_ADDRESS

    state = await salary.on_employer_address(
        _make_message_update("04050, м. Київ, вул. Тестова, 1"), ctx
    )
    assert state == S.AMOUNT

    state = await salary.on_amount(_make_message_update("15000"), ctx)
    assert state == S.PERIOD_FROM

    state = await salary.on_period_from(_make_message_update("01.2026"), ctx)
    assert state == S.PERIOD_TO

    state = await salary.on_period_to(_make_message_update("03.2026"), ctx)
    assert state == S.LAST_PAYMENT_DATE

    state = await salary.on_last_payment_date(_make_message_update("15.12.2025"), ctx)
    assert state == S.USER_NAME

    state = await salary.on_user_name(_make_message_update("Іваненко Іван Іванович"), ctx)
    assert state == S.USER_TAX_ID

    state = await salary.on_user_tax_id(_make_message_update("1234567890"), ctx)
    assert state == S.USER_ADDRESS

    state = await salary.on_user_address(
        _make_message_update("03150, м. Київ, вул. Друга, 2"), ctx
    )
    assert state == S.USER_PHONE

    # Останнє питання → переходимо у PREVIEW
    state = await salary.on_user_phone(_make_message_update("+380501234567"), ctx)
    assert state == S.PREVIEW

    # Прев'ю показано
    data = ctx.user_data["salary"]
    assert data["employer_name"] == "ТОВ Тест"
    assert data["user_phone"] == "+380501234567"

    # Підтвердити preview → ідемо в PRE_GENERATE (попередження + 2 опції)
    state = await salary.on_preview_confirm(_make_callback_update("preview:confirm"), ctx)
    assert state == S.PRE_GENERATE

    # Обираємо «Отримати шаблон» → переходимо в PLAN_CHOICE
    state = await salary.on_pregen_template(_make_callback_update("pregen:template"), ctx)
    assert state == S.PLAN_CHOICE


@pytest.mark.asyncio
async def test_edit_field_returns_to_preview(env):
    ctx = _make_context()
    # Заповнюємо дані напряму, оминаючи 11 кроків
    from datetime import date
    from decimal import Decimal

    ctx.user_data["salary"] = {
        "employer_name": "Старе",
        "employer_edrpou": "12345678",
        "employer_address": "стара адреса довша 10 символів",
        "amount": Decimal("100"),
        "period_from": (1, 2026),
        "period_to": (1, 2026),
        "last_payment_date": date(2026, 1, 15),
        "user_name": "Іваненко Іван",
        "user_tax_id": "1234567890",
        "user_address": "адреса користувача 100",
        "user_phone": "+380501234567",
    }

    # Симулюємо клік "Виправити поле" → "employer_name"
    state = await salary.on_edit_field(
        _make_callback_update("edit_field:employer_name"), ctx
    )
    assert state == S.EMPLOYER_NAME
    assert ctx.user_data[salary.EDITING_KEY] == "employer_name"

    # Вводимо нове значення → має повернутись на PREVIEW, а не йти на EDRPOU
    state = await salary.on_employer_name(_make_message_update("Нове ТОВ"), ctx)
    assert state == S.PREVIEW
    assert ctx.user_data["salary"]["employer_name"] == "Нове ТОВ"
    assert salary.EDITING_KEY not in ctx.user_data

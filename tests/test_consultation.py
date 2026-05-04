"""E2E тести форми консультації."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pravohelp.handlers import consultation
from pravohelp.handlers.consultation import C
from pravohelp.storage.db import get_session, init_db
from pravohelp.storage.models import ConsultationRequest


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    init_db(f"sqlite:///{db_file}")
    yield


def _make_message_update(text: str, *, telegram_id: int = 42):
    msg = MagicMock()
    msg.text = text
    msg.reply_html = AsyncMock()
    msg.reply_text = AsyncMock()
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
    query = MagicMock()
    query.data = data
    query.message = msg
    query.answer = AsyncMock()

    user = MagicMock()
    user.id = telegram_id
    user.username = "tester"

    update = MagicMock()
    update.message = None
    update.callback_query = query
    update.effective_user = user
    return update


def _make_context_with_bot():
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_full_consultation_flow_no_lawyer_id(env, monkeypatch):
    monkeypatch.delenv("LAWYER_TELEGRAM_ID", raising=False)

    ctx = _make_context_with_bot()

    # Старт з кнопки "consult_start:labor"
    state = await consultation.start_consultation(
        _make_callback_update("consult_start:labor"), ctx
    )
    assert state == C.NAME

    state = await consultation.on_name(_make_message_update("Іваненко Іван Іванович"), ctx)
    assert state == C.PHONE

    state = await consultation.on_phone(_make_message_update("+380501234567"), ctx)
    assert state == C.DESCRIPTION

    state = await consultation.on_description(
        _make_message_update("Не виплачують зарплату 3 місяці, керівник не реагує"), ctx
    )
    assert state == C.CONFIRM

    # Підтверджуємо
    state = await consultation.on_confirm_send(_make_callback_update("consult:send"), ctx)
    assert state == -1  # ConversationHandler.END

    # У БД має бути одна заявка, без dispatched_at (бо LAWYER_TELEGRAM_ID не задано)
    with get_session() as s:
        reqs = s.query(ConsultationRequest).all()
        assert len(reqs) == 1
        req = reqs[0]
        assert req.field == "labor"
        assert req.name == "Іваненко Іван Іванович"
        assert req.phone == "+380501234567"
        assert req.dispatched_at is None

    # bot.send_message не викликався — бо нема куди
    ctx.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_consultation_pushes_to_lawyer_when_id_set(env, monkeypatch):
    monkeypatch.setenv("LAWYER_TELEGRAM_ID", "5555")

    ctx = _make_context_with_bot()

    await consultation.start_consultation(_make_callback_update("consult_start:family"), ctx)
    await consultation.on_name(_make_message_update("Петренко Петро"), ctx)
    await consultation.on_phone(_make_message_update("+380671112233"), ctx)
    await consultation.on_description(_make_message_update("Питання щодо аліментів"), ctx)
    await consultation.on_confirm_send(_make_callback_update("consult:send"), ctx)

    # Push виконано на правильний chat_id
    ctx.bot.send_message.assert_called_once()
    call_kwargs = ctx.bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 5555
    assert "Петренко Петро" in call_kwargs["text"]
    assert "Сімейне право" in call_kwargs["text"]

    # У БД заявка з dispatched_at
    with get_session() as s:
        req = s.query(ConsultationRequest).one()
        assert req.dispatched_at is not None


@pytest.mark.asyncio
async def test_consultation_invalid_field_aborts(env):
    ctx = _make_context_with_bot()
    state = await consultation.start_consultation(
        _make_callback_update("consult_start:nonexistent"), ctx
    )
    assert state == -1  # END

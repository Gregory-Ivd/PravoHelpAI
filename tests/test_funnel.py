"""Тести емітів воронки в ключових точках сценаріїв."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from pravohelp.handlers import consultation, salary, start
from pravohelp.handlers.consultation import C
from pravohelp.handlers.salary import S
from pravohelp.storage.db import get_session, init_db
from pravohelp.storage.models import User


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.delenv("LAWYER_TELEGRAM_ID", raising=False)
    init_db(f"sqlite:///{db_file}")
    yield


def _msg_update(text: str = "", *, telegram_id: int = 42):
    msg = MagicMock()
    msg.text = text
    msg.reply_html = AsyncMock()
    msg.reply_text = AsyncMock()
    u = MagicMock()
    u.message = msg
    u.callback_query = None
    u.effective_user = MagicMock(id=telegram_id, username="tester")
    return u


def _cb_update(data: str, *, telegram_id: int = 42):
    msg = MagicMock()
    msg.reply_html = AsyncMock()
    msg.reply_text = AsyncMock()
    q = MagicMock()
    q.data = data
    q.message = msg
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    u = MagicMock()
    u.message = None
    u.callback_query = q
    u.effective_user = MagicMock(id=telegram_id, username="tester")
    return u


def _ctx():
    c = MagicMock()
    c.user_data = {}
    c.bot = MagicMock()
    c.bot.send_message = AsyncMock()
    return c


def _events(captured: list[dict]) -> list[str]:
    return [e["event"] for e in captured]


@pytest.mark.asyncio
async def test_disclaimer_accept_emits_event(env):
    with get_session() as s:
        s.add(User(telegram_id=42))
    with structlog.testing.capture_logs() as captured:
        await start.on_disclaimer_accept(_cb_update("disclaimer:accept"), _ctx())
    assert "disclaimer_accepted" in _events(captured)


@pytest.mark.asyncio
async def test_disclaimer_decline_emits_event(env):
    with structlog.testing.capture_logs() as captured:
        await start.on_disclaimer_decline(_cb_update("disclaimer:decline"), _ctx())
    assert "disclaimer_declined" in _events(captured)


@pytest.mark.asyncio
async def test_salary_step_emitted_with_field(env):
    ctx = _ctx()
    ctx.user_data["salary"] = {}
    with structlog.testing.capture_logs() as captured:
        await salary.on_employer_name(_msg_update("ТОВ «Промінь»"), ctx)
    step_events = [e for e in captured if e["event"] == "salary_step"]
    assert len(step_events) == 1
    assert step_events[0]["field"] == "employer_name"
    assert step_events[0]["editing"] is False


@pytest.mark.asyncio
async def test_salary_preview_cancel_emits(env):
    with structlog.testing.capture_logs() as captured:
        await salary.on_preview_cancel(_cb_update("preview:cancel"), _ctx())
    cancel_events = [e for e in captured if e["event"] == "salary_cancelled"]
    assert len(cancel_events) == 1
    assert cancel_events[0]["source"] == "preview"


@pytest.mark.asyncio
async def test_consult_started_emits_field(env):
    with structlog.testing.capture_logs() as captured:
        state = await consultation.start_consultation(
            _cb_update("consult_start:labor"), _ctx()
        )
    assert state == C.NAME
    started = [e for e in captured if e["event"] == "consult_started"]
    assert len(started) == 1
    assert started[0]["field"] == "labor"


@pytest.mark.asyncio
async def test_consult_submitted_emits_dispatched_false_when_no_lawyer(env):
    ctx = _ctx()
    await consultation.start_consultation(_cb_update("consult_start:labor"), ctx)
    await consultation.on_name(_msg_update("Іваненко Іван Іванович"), ctx)
    await consultation.on_phone(_msg_update("+380501234567"), ctx)
    await consultation.on_description(_msg_update("Опис проблеми достатньої довжини"), ctx)
    with structlog.testing.capture_logs() as captured:
        await consultation.on_confirm_send(_cb_update("consult:send"), ctx)
    submitted = [e for e in captured if e["event"] == "consult_submitted"]
    assert len(submitted) == 1
    assert submitted[0]["dispatched"] is False
    assert submitted[0]["field"] == "labor"


@pytest.mark.asyncio
async def test_funnel_does_not_log_pii(env):
    """Жодне поле події воронки не має містити введених текстових даних."""
    ctx = _ctx()
    pii = "Іваненко Олександр Сергійович"
    ctx.user_data["salary"] = {}
    with structlog.testing.capture_logs() as captured:
        await salary.on_user_name(_msg_update(pii), ctx)
    for e in captured:
        for v in e.values():
            assert pii not in str(v), f"PII leak in event: {e}"


@pytest.mark.asyncio
async def test_salary_advance_state_returned_unchanged(env):
    """Сейфті: додавання funnel-емітів не зламало повернений FSM-стан."""
    ctx = _ctx()
    ctx.user_data["salary"] = {}
    state = await salary.on_employer_name(_msg_update("ТОВ «Промінь»"), ctx)
    assert state == S.EMPLOYER_EDRPOU

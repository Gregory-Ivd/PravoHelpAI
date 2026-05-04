"""Тести команди /history."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from pravohelp.handlers.history import cmd_history
from pravohelp.storage.db import get_session, init_db
from pravohelp.storage.models import ScenarioRequest, User


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    init_db(f"sqlite:///{db_file}")
    yield


def _update(*, telegram_id: int = 42):
    msg = MagicMock()
    msg.reply_html = AsyncMock()
    msg.reply_text = AsyncMock()
    u = MagicMock()
    u.message = msg
    u.effective_user = MagicMock(id=telegram_id, username="tester")
    return u


@pytest.mark.asyncio
async def test_history_no_user(env):
    update = _update(telegram_id=999)
    await cmd_history(update, MagicMock())
    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args.args[0]
    assert "немає історії" in text or "першого" in text


@pytest.mark.asyncio
async def test_history_user_with_no_requests(env):
    with get_session() as s:
        s.add(User(telegram_id=42))
    update = _update(telegram_id=42)
    await cmd_history(update, MagicMock())
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_history_shows_completed_request(env):
    with get_session() as s:
        u = User(telegram_id=42)
        s.add(u)
        s.flush()
        s.add(ScenarioRequest(
            user_id=u.id,
            scenario="salary",
            status="completed",
            plan_chosen="court",
            documents_generated=1,
            completed_at=datetime.now(UTC),
        ))

    update = _update(telegram_id=42)
    await cmd_history(update, MagicMock())
    update.message.reply_html.assert_called_once()
    text = update.message.reply_html.call_args.args[0]
    assert "Невиплата зарплати" in text
    assert "Завершено" in text
    assert "Позов до суду" in text


@pytest.mark.asyncio
async def test_history_does_not_leak_pii(env):
    """У відповіді /history не має бути жодних PII — модель ScenarioRequest їх і не зберігає."""
    with get_session() as s:
        u = User(telegram_id=42, username="ivan_secret")
        s.add(u)
        s.flush()
        s.add(ScenarioRequest(
            user_id=u.id,
            scenario="salary",
            status="completed",
            plan_chosen="employer",
            documents_generated=1,
            completed_at=datetime.now(UTC),
        ))

    update = _update(telegram_id=42)
    await cmd_history(update, MagicMock())
    text = update.message.reply_html.call_args.args[0]
    # Username — це публічно у Telegram, але як санітарна перевірка теж не має витекти
    assert "ivan_secret" not in text


@pytest.mark.asyncio
async def test_history_limit(env):
    """При >10 записах показуємо лише останні 10."""
    with get_session() as s:
        u = User(telegram_id=42)
        s.add(u)
        s.flush()
        for _ in range(15):
            s.add(ScenarioRequest(
                user_id=u.id,
                scenario="salary",
                status="completed",
                plan_chosen="employer",
                documents_generated=1,
                completed_at=datetime.now(UTC),
            ))

    update = _update(telegram_id=42)
    await cmd_history(update, MagicMock())
    text = update.message.reply_html.call_args.args[0]
    # Кожен запис починається з "•"
    assert text.count("•") == 10

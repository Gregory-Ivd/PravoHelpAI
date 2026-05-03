from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pravohelp.handlers import admin
from pravohelp.storage.db import get_session, init_db
from pravohelp.storage.models import ScenarioRequest, User


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    init_db(f"sqlite:///{db_file}")
    yield


def _make_update(telegram_id: int):
    user = MagicMock()
    user.id = telegram_id
    msg = MagicMock()
    msg.reply_html = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.message = msg
    return update, msg


@pytest.mark.asyncio
async def test_stats_denied_for_non_admin(db, monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
    update, msg = _make_update(telegram_id=999)

    await admin.cmd_stats(update, MagicMock())

    msg.reply_html.assert_not_called()


@pytest.mark.asyncio
async def test_stats_renders_for_admin(db, monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "918580618")

    with get_session() as s:
        u = User(telegram_id=918580618)
        s.add(u)
        s.flush()
        s.add(ScenarioRequest(
            user_id=u.id, scenario="salary", status="completed",
            plan_chosen="employer", documents_generated=1,
        ))

    update, msg = _make_update(telegram_id=918580618)
    await admin.cmd_stats(update, MagicMock())

    msg.reply_html.assert_called_once()
    text = msg.reply_html.call_args[0][0]
    assert "Статистика" in text
    assert "Користувачі" in text
    assert "Завершені сценарії" in text
    assert "Претензія роботодавцю" in text

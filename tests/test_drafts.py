from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from pravohelp.storage.db import init_db
from pravohelp.storage.drafts import cleanup_old_drafts, delete_draft, load_draft, save_draft
from pravohelp.storage.models import ScenarioDraft


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    init_db(f"sqlite:///{db_file}")
    yield


def _sample_data() -> dict:
    return {
        "employer_name": "ТОВ «Тест»",
        "employer_edrpou": "12345678",
        "amount": Decimal("15000.50"),
        "period_from": (1, 2026),
        "period_to": (3, 2026),
        "last_payment_date": date(2025, 12, 15),
        "started_at": datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
    }


def test_save_then_load_roundtrip(db):
    data = _sample_data()
    save_draft(111, "salary", state=104, data=data)

    loaded = load_draft(111, "salary")
    assert loaded is not None

    state, restored = loaded
    assert state == 104
    assert restored["employer_name"] == "ТОВ «Тест»"
    assert restored["employer_edrpou"] == "12345678"
    assert restored["amount"] == Decimal("15000.50")
    assert restored["period_from"] == (1, 2026)
    assert restored["last_payment_date"] == date(2025, 12, 15)
    assert restored["started_at"] == datetime(2026, 5, 3, 10, 0, tzinfo=UTC)


def test_save_overwrites_existing(db):
    save_draft(111, "salary", state=100, data={"employer_name": "old"})
    save_draft(111, "salary", state=103, data={"employer_name": "new"})

    state, data = load_draft(111, "salary")
    assert state == 103
    assert data["employer_name"] == "new"


def test_load_returns_none_for_missing(db):
    assert load_draft(999, "salary") is None


def test_delete_draft(db):
    save_draft(111, "salary", state=100, data={"employer_name": "x"})
    delete_draft(111, "salary")
    assert load_draft(111, "salary") is None


def test_per_user_isolation(db):
    save_draft(111, "salary", state=100, data={"employer_name": "user-111"})
    save_draft(222, "salary", state=100, data={"employer_name": "user-222"})

    _, data_111 = load_draft(111, "salary")
    _, data_222 = load_draft(222, "salary")
    assert data_111["employer_name"] == "user-111"
    assert data_222["employer_name"] == "user-222"


def test_cleanup_removes_old_only(db):
    from pravohelp.storage.db import get_session

    save_draft(111, "salary", state=100, data={"employer_name": "old"})
    save_draft(222, "salary", state=100, data={"employer_name": "fresh"})

    # Підкручуємо updated_at старшої чернетки
    with get_session() as session:
        old = session.query(ScenarioDraft).filter_by(telegram_id=111).one()
        old.updated_at = datetime.now(UTC) - timedelta(hours=48)

    removed = cleanup_old_drafts(ttl_hours=24)
    assert removed == 1
    assert load_draft(111, "salary") is None
    assert load_draft(222, "salary") is not None

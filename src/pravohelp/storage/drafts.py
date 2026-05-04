"""CRUD для чернеток сценаріїв."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import delete, select

from pravohelp.storage.db import get_session
from pravohelp.storage.models import ScenarioDraft

log = structlog.get_logger(__name__)

DRAFT_TTL_HOURS = 24


def _encode(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return {"__type__": "decimal", "value": str(obj)}
    if isinstance(obj, datetime):
        return {"__type__": "datetime", "value": obj.isoformat()}
    if isinstance(obj, date):
        return {"__type__": "date", "value": obj.isoformat()}
    if isinstance(obj, tuple):
        return {"__type__": "tuple", "value": [_encode(x) for x in obj]}
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_encode(x) for x in obj]
    return obj


def _decode(obj: Any) -> Any:
    if isinstance(obj, dict):
        marker = obj.get("__type__")
        if marker == "decimal":
            return Decimal(obj["value"])
        if marker == "datetime":
            return datetime.fromisoformat(obj["value"])
        if marker == "date":
            return date.fromisoformat(obj["value"])
        if marker == "tuple":
            return tuple(_decode(x) for x in obj["value"])
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(x) for x in obj]
    return obj


def save_draft(telegram_id: int, scenario: str, state: int, data: dict[str, Any]) -> None:
    payload = json.dumps(_encode(data), ensure_ascii=False)
    with get_session() as session:
        existing = session.scalar(
            select(ScenarioDraft).where(
                ScenarioDraft.telegram_id == telegram_id,
                ScenarioDraft.scenario == scenario,
            )
        )
        if existing is None:
            session.add(
                ScenarioDraft(
                    telegram_id=telegram_id,
                    scenario=scenario,
                    state=state,
                    data_json=payload,
                )
            )
        else:
            existing.state = state
            existing.data_json = payload


def load_draft(telegram_id: int, scenario: str) -> tuple[int, dict[str, Any]] | None:
    with get_session() as session:
        draft = session.scalar(
            select(ScenarioDraft).where(
                ScenarioDraft.telegram_id == telegram_id,
                ScenarioDraft.scenario == scenario,
            )
        )
        if draft is None:
            return None
        return draft.state, _decode(json.loads(draft.data_json))


def delete_draft(telegram_id: int, scenario: str) -> None:
    with get_session() as session:
        session.execute(
            delete(ScenarioDraft).where(
                ScenarioDraft.telegram_id == telegram_id,
                ScenarioDraft.scenario == scenario,
            )
        )


def cleanup_old_drafts(ttl_hours: int = DRAFT_TTL_HOURS) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    with get_session() as session:
        result = session.execute(
            delete(ScenarioDraft).where(ScenarioDraft.updated_at < cutoff)
        )
        removed = result.rowcount or 0

    if removed:
        log.info("drafts_cleanup_done", removed=removed)
    return removed

"""Структуроване логування подій воронки.

Усі події йдуть у виділений logger 'funnel' як structlog-події з полем event=<назва>.
Жодних PII (ПІБ/телефон/ІПН/адреса) у kwargs — лише telegram_id, назва кроку, обраний план тощо.
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("funnel")


def emit(event: str, *, telegram_id: int | None = None, **fields: Any) -> None:
    if telegram_id is not None:
        fields["telegram_id"] = telegram_id
    _log.info(event, **fields)

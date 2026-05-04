"""Щоденний бекап SQLite-БД у data/backups/."""

from __future__ import annotations

import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog

from pravohelp.config import PROJECT_ROOT, load_settings

log = structlog.get_logger(__name__)

BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
BACKUP_TTL_DAYS = 14


def _db_path() -> Path | None:
    url = load_settings().database_url
    if not url.startswith("sqlite:///"):
        return None
    rel = url[len("sqlite:///") :]
    return (PROJECT_ROOT / rel).resolve() if not Path(rel).is_absolute() else Path(rel)


def backup_db() -> Path | None:
    """Скопіювати БД у data/backups/db_YYYY-MM-DD.db. Повертає шлях або None."""
    src = _db_path()
    if src is None or not src.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    dst = BACKUP_DIR / f"db_{today}.db"

    shutil.copy2(src, dst)
    log.info("db_backup_done", path=str(dst), size=dst.stat().st_size)
    return dst


def cleanup_old_backups(ttl_days: int = BACKUP_TTL_DAYS) -> int:
    if not BACKUP_DIR.exists():
        return 0
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for path in BACKUP_DIR.glob("db_*.db"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            log.warning("backup_cleanup_failed", path=str(path))

    if removed:
        log.info("backups_cleanup_done", removed=removed)
    return removed

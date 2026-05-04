from __future__ import annotations

import os
import time

import pytest

from pravohelp.storage import backup as backup_module


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    db_file = tmp_path / "src.db"
    db_file.write_bytes(b"sqlite-data")
    backups = tmp_path / "backups"

    monkeypatch.setattr(backup_module, "_db_path", lambda: db_file)
    monkeypatch.setattr(backup_module, "BACKUP_DIR", backups)

    return db_file, backups


def test_backup_creates_dated_file(fake_db):
    db_file, backups = fake_db
    result = backup_module.backup_db()
    assert result is not None
    assert result.parent == backups
    assert result.name.startswith("db_") and result.name.endswith(".db")
    assert result.read_bytes() == b"sqlite-data"


def test_backup_returns_none_if_db_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "_db_path", lambda: tmp_path / "nope.db")
    monkeypatch.setattr(backup_module, "BACKUP_DIR", tmp_path / "backups")
    assert backup_module.backup_db() is None


def test_cleanup_removes_old_backups(fake_db):
    _, backups = fake_db
    backups.mkdir(parents=True, exist_ok=True)

    old = backups / "db_2026-01-01.db"
    fresh = backups / "db_2026-05-03.db"
    old.write_bytes(b"old")
    fresh.write_bytes(b"fresh")

    long_ago = time.time() - 30 * 86400
    os.utime(old, (long_ago, long_ago))

    removed = backup_module.cleanup_old_backups(ttl_days=14)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()

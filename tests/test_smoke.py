from __future__ import annotations

import os

import pytest


def test_imports():
    """Перевіряємо, що пакет імпортується без синтаксичних помилок."""
    import pravohelp
    import pravohelp.config  # noqa: F401
    import pravohelp.handlers.start  # noqa: F401
    import pravohelp.storage.db  # noqa: F401
    import pravohelp.storage.models  # noqa: F401

    assert pravohelp.__version__ == "0.1.0"


def test_settings_requires_token(monkeypatch):
    """Без TELEGRAM_BOT_TOKEN load_settings має падати з осмисленим повідомленням."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from pravohelp.config import load_settings

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_settings()


def test_settings_loads_with_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    from pravohelp.config import load_settings

    settings = load_settings()
    assert settings.telegram_bot_token == "123:test"
    assert settings.log_level == "DEBUG"
    assert settings.lawyer_name == "Дмитро Глушко"


def test_db_init_creates_tables(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    from pravohelp.storage.db import init_db

    init_db(f"sqlite:///{db_file}")
    assert db_file.exists() or os.path.exists(str(db_file))

import asyncio
import os

import pytest

from app.db import session as db_session
from app import main


def test_resolve_database_url_forces_test_db_when_pytest_env(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "safety/test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./imprint.db")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    resolved = db_session.resolve_database_url()

    assert "imprint_test" in resolved or "mode=memory" in resolved


def test_init_models_refuses_reset_on_non_test_db(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "safety/test")
    monkeypatch.setenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///./imprint.db")

    with pytest.raises(RuntimeError):
        asyncio.run(main.init_models())

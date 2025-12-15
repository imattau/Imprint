import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import models
from app.db import session as db_session


@pytest.fixture()
def admin_client(monkeypatch, tmp_path) -> TestClient:
    db_path = tmp_path / "backup.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine: AsyncEngine = create_async_engine(database_url)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session, "aengine", engine)
    monkeypatch.setattr(db_session, "AsyncSessionLocal", AsyncSessionLocal)
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    monkeypatch.setattr(main, "indexer_task", None)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_create())

    with TestClient(main.app) as client:
        # login as admin
        csrf = _extract_csrf(client.get("/admin").text)
        client.post("/admin/login", data={"admin_token_input": "secret-token", "csrf": csrf})
        yield client

    loop.run_until_complete(engine.dispose())
    asyncio.set_event_loop(None)


def _extract_csrf(html: str) -> str:
    import re

    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_backup_create_returns_zip(admin_client):
    csrf = _extract_csrf(admin_client.get("/admin/backup").text)
    resp = admin_client.post("/admin/backup/create", data={"csrf": csrf})
    assert resp.status_code == 200
    buf = io.BytesIO(resp.content)
    with zipfile.ZipFile(buf, "r") as zf:
        names = set(zf.namelist())
        assert {"database.sqlite3", "settings.json", "meta.json"}.issubset(names)


def test_restore_rejects_invalid_zip(admin_client):
    csrf = _extract_csrf(admin_client.get("/admin/backup").text)
    resp = admin_client.post(
        "/admin/restore/apply",
        data={"confirm": "RESTORE", "csrf": csrf},
        files={"archive": ("bad.txt", b"not-a-zip", "text/plain")},
    )
    assert resp.status_code == 400

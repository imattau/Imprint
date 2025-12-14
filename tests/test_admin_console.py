import asyncio
import os
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import models
from app.db import session as db_session
from app.nostr.key import encode_npub


@pytest.fixture()
def client(monkeypatch, tmp_path) -> TestClient:
    db_path = tmp_path / "test_admin.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine: AsyncEngine = create_async_engine(database_url)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session, "aengine", engine)
    monkeypatch.setattr(db_session, "AsyncSessionLocal", AsyncSessionLocal)
    monkeypatch.setattr(main, "indexer_task", None)
    monkeypatch.setattr(main.settings, "relay_urls", [])

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "init_models", _noop)
    monkeypatch.setattr(main, "run_indexer", _noop)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_create())

    with TestClient(main.app) as test_client:
        yield test_client

    asyncio.get_event_loop().run_until_complete(engine.dispose())


def _extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_non_admin_cannot_access_settings(client):
    resp = client.get("/admin/settings")
    assert resp.status_code == 403


def test_admin_token_login_allows_access(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    resp = client.get("/admin")
    csrf = _extract_csrf(resp.text)
    login = client.post("/admin/login", data={"admin_token_input": "secret-token", "csrf": csrf}, follow_redirects=True)
    assert login.status_code in (200, 303)
    settings_page = client.get("/admin/settings")
    assert settings_page.status_code == 200
    assert "Instance settings" in settings_page.text


def test_admin_allowlist_grants_access(client, monkeypatch):
    allowed_npub = encode_npub("1" * 64)
    monkeypatch.setenv("ADMIN_NPUBS", allowed_npub)
    login = client.post(
        "/auth/login/readonly",
        data={"npub": allowed_npub, "duration": "15m"},
        follow_redirects=True,
    )
    assert login.status_code in (200, 303)
    resp = client.get("/admin", allow_redirects=True)
    assert resp.status_code == 200
    assert "Admin console" in resp.text


def test_settings_persist_and_reflect_in_templates(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    csrf = _extract_csrf(client.get("/admin").text)
    client.post("/admin/login", data={"admin_token_input": "secret-token", "csrf": csrf})
    settings_page = client.get("/admin/settings")
    form_csrf = _extract_csrf(settings_page.text)
    save = client.post(
        "/admin/settings",
        data={
            "site_name": "Imprint Test",
            "site_tagline": "Admin managed",
            "site_description": "A curated feed.",
            "default_relays": "wss://example.com",
            "max_feed_items": "5",
            "session_default_minutes": "45",
            "enable_public_essays_feed": "on",
            "enable_registrationless_readonly": "on",
            "enable_payments": "on",
            "lightning_address": "tip@example.com",
            "csrf": form_csrf,
        },
    )
    assert save.status_code == 200
    home = client.get("/")
    assert "Imprint Test" in home.text
    assert "tip@example.com" in home.text


def test_validation_rejects_invalid_inputs(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    csrf = _extract_csrf(client.get("/admin").text)
    client.post("/admin/login", data={"admin_token_input": "secret-token", "csrf": csrf})
    settings_page = client.get("/admin/settings")
    form_csrf = _extract_csrf(settings_page.text)
    resp = client.post(
        "/admin/settings",
        data={
            "site_name": "Test",
            "instance_admin_npub": "invalidnpub",
            "public_base_url": "ftp://example.com",
            "csrf": form_csrf,
        },
    )
    assert resp.status_code == 400
    assert "Invalid npub format" in resp.text or "Public base URL" in resp.text

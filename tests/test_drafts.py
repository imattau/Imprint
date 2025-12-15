import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.session import get_session
from app.main import app, init_models


def make_client() -> TestClient:
    asyncio.run(init_models())
    return TestClient(app)


async def _first_draft():
    async with get_session() as session:
        result = await session.execute(select(models.Draft))
        return result.scalars().first()


def test_save_and_list_draft():
    client = make_client()
    client.post(
        "/auth/login/nip07",
        json={"pubkey": "a" * 64, "duration": "1h"},
        headers={"HX-Request": "true"},
    )
    resp = client.post(
        "/drafts/save",
        data={"title": "My Draft", "content": "Content", "summary": "", "identifier": "draft-1", "tags": "nostr"},
        allow_redirects=False,
    )
    assert resp.status_code == 303
    page = client.get("/drafts").text
    assert "My Draft" in page


def test_publish_draft_requires_signer_not_readonly():
    client = make_client()
    client.post(
        "/auth/login/nip07",
        json={"pubkey": "b" * 64, "duration": "1h"},
        headers={"HX-Request": "true"},
    )
    client.post(
        "/drafts/save",
        data={"title": "Draft", "content": "Content", "summary": "", "identifier": "draft-ro"},
        allow_redirects=False,
    )
    draft = asyncio.run(_first_draft())
    client.post(
        "/auth/login/readonly",
        data={"npub": "npub1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqg3l8da", "duration": "1h"},
        headers={"HX-Request": "true"},
    )
    resp = client.post(f"/drafts/{draft.id}/publish", allow_redirects=False)
    assert resp.status_code == 403


def test_history_shows_published_post(monkeypatch):
    client = make_client()
    monkeypatch.setattr("app.config.settings.nostr_secret", "1" * 64)
    resp = client.post("/auth/login/local", data={"duration": "1h"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    publish_resp = client.post(
        "/publish",
        data={"title": "History Post", "content": "hello", "summary": "", "identifier": "hist-1", "tags": ""},
        allow_redirects=False,
    )
    assert publish_resp.status_code in (302, 303)
    history = client.get("/history").text
    assert "History Post" in history

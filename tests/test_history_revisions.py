import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.session import get_session
from app.main import app, init_models


def make_client() -> TestClient:
    asyncio.run(init_models())
    return TestClient(app)


def publish_sample(client: TestClient, identifier: str, title: str, content: str):
    resp = client.post(
        "/publish",
        data={"title": title, "content": content, "summary": "", "identifier": identifier, "tags": ""},
        allow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def test_history_dedupes_latest(monkeypatch):
    client = make_client()
    monkeypatch.setattr("app.config.settings.nostr_secret", "3" * 64)
    client.post("/auth/login/local", data={"duration": "1h"}, headers={"HX-Request": "true"})
    publish_sample(client, "rev-1", "First", "v1")
    publish_sample(client, "rev-1", "First", "v2")

    history = client.get("/history").text
    # Only latest should show (v2).
    assert history.count("rev-1") == 1


def test_revisions_list(monkeypatch):
    client = make_client()
    monkeypatch.setattr("app.config.settings.nostr_secret", "4" * 64)
    client.post("/auth/login/local", data={"duration": "1h"}, headers={"HX-Request": "true"})
    publish_sample(client, "rev-2", "Title", "a1")
    publish_sample(client, "rev-2", "Title", "a2")

    page = client.get("/history/rev-2/revisions").text
    # Both revisions present.
    assert page.count("Event:") >= 2


def test_revert_creates_new_revision(monkeypatch):
    client = make_client()
    monkeypatch.setattr("app.config.settings.nostr_secret", "5" * 64)
    client.post("/auth/login/local", data={"duration": "1h"}, headers={"HX-Request": "true"})
    publish_sample(client, "rev-3", "T", "orig")
    publish_sample(client, "rev-3", "T", "changed")

    # Grab first revision event id (orig).
    async def _first_event():
        async with get_session() as session:
            result = await session.execute(
                select(models.EssayVersion)
                .join(models.Essay)
                .where(models.Essay.identifier == "rev-3")
                .order_by(models.EssayVersion.published_at.asc())
            )
            return result.scalars().first()

    first = asyncio.run(_first_event())
    resp = client.post(f"/history/rev-3/revisions/{first.event_id}/revert", allow_redirects=False)
    assert resp.status_code in (302, 303)

    async def _latest_content():
        async with get_session() as session:
            result = await session.execute(
                select(models.EssayVersion)
                .join(models.Essay)
                .where(models.Essay.identifier == "rev-3")
                .order_by(models.EssayVersion.version.desc())
            )
            return result.scalars().first().content

    latest_content = asyncio.run(_latest_content())
    assert "orig" in latest_content

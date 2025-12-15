import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app, init_models
from app.db import models
from app.db.session import get_session


def make_client() -> TestClient:
    asyncio.run(init_models())
    return TestClient(app)


def _latest_event_id():
    async def _fetch():
        async with get_session() as session:
            result = await session.execute(select(models.EssayVersion).order_by(models.EssayVersion.id.desc()))
            version = result.scalars().first()
            return version.event_id if version else None

    return asyncio.run(_fetch())


def publish_sample(client: TestClient, monkeypatch) -> str:
    monkeypatch.setattr("app.config.settings.nostr_secret", "1" * 64)
    resp = client.post("/auth/login/local", data={"duration": "1h"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    resp = client.post(
        "/publish",
        data={
            "title": "Test",
            "content": "Hello world",
            "summary": "",
            "identifier": "",
            "tags": "",
            "action": "publish",
        },
        allow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    event_id = _latest_event_id()
    assert event_id
    return event_id


def test_like_endpoint_updates_count(monkeypatch):
    client = make_client()
    event_id = publish_sample(client, monkeypatch)

    resp = client.post(f"/posts/{event_id}/like", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert ">1<" in resp.text or "count\">1" in resp.text

    resp2 = client.post(f"/posts/{event_id}/like", headers={"HX-Request": "true"})
    assert resp2.status_code == 200
    assert ">0<" in resp2.text or "count\">0" in resp2.text


def test_zap_endpoint_updates_totals(monkeypatch):
    client = make_client()
    event_id = publish_sample(client, monkeypatch)

    resp = client.post(
        f"/posts/{event_id}/zap",
        data={"amount": 500},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "⚡" in resp.text
    assert "500" in resp.text

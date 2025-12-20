import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from ecdsa import SECP256k1, SigningKey

from app.config import settings
from app.db import models
from app.db.session import get_session
from app.main import app, init_models


def make_client() -> TestClient:
    return TestClient(app)


def _valid_secret() -> str:
    sk = SigningKey.generate(curve=SECP256k1)
    return sk.to_string().hex()


def login_local(client: TestClient, _: str):
    settings.nostr_secret = _valid_secret()
    resp = client.post("/auth/login/local", data={"duration": "1h"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200


def _latest_version():
    async def _fetch():
        async with get_session() as session:
            result = await session.execute(select(models.EssayVersion).order_by(models.EssayVersion.id.desc()))
            return result.scalars().first()

    return asyncio.run(_fetch())


def test_draft_identifier_owned_by_author():
    author = make_client()
    attacker = make_client()
    login_local(author, "1" * 64)
    resp = author.post(
        "/drafts/save",
        data={"title": "Mine", "content": "draft", "summary": "", "identifier": "owned-id"},
        allow_redirects=False,
    )
    assert resp.status_code == 303

    login_local(attacker, "2" * 64)
    resp2 = attacker.post(
        "/drafts/save",
        data={"title": "Hijack", "content": "hijack", "summary": "", "identifier": "owned-id"},
        allow_redirects=False,
    )
    assert resp2.status_code == 403


def test_publish_blocks_non_owner_identifier():
    author = make_client()
    attacker = make_client()
    login_local(author, "3" * 64)
    resp = author.post(
        "/publish",
        data={"title": "Owned", "content": "content", "summary": "", "identifier": "owner-post", "tags": ""},
        allow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    login_local(attacker, "4" * 64)
    resp2 = attacker.post(
        "/publish",
        data={"title": "Hijack", "content": "nope", "summary": "", "identifier": "owner-post", "tags": ""},
        allow_redirects=False,
    )
    assert resp2.status_code == 403


def test_draft_publish_requires_owner():
    author = make_client()
    attacker = make_client()
    login_local(author, "5" * 64)
    resp = author.post(
        "/drafts/save",
        data={"title": "Draft", "content": "draft", "summary": "", "identifier": "draft-owner"},
        allow_redirects=False,
    )
    assert resp.status_code == 303
    # Drafts are stored separately; fetch the draft id directly.
    async def _draft():
        async with get_session() as session:
            result = await session.execute(select(models.Draft).order_by(models.Draft.id.desc()))
            return result.scalars().first()

    draft_obj = asyncio.run(_draft())
    assert draft_obj is not None

    login_local(attacker, "6" * 64)
    resp2 = attacker.post(f"/drafts/{draft_obj.id}/publish", allow_redirects=False)
    assert resp2.status_code in (403, 404)


def test_revert_blocks_non_owner(monkeypatch):
    author = make_client()
    attacker = make_client()
    login_local(author, "7" * 64)
    resp = author.post(
        "/publish",
        data={"title": "RevertTest", "content": "v1", "summary": "", "identifier": "revert-id", "tags": ""},
        allow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    version = _latest_version()
    assert version is not None
    event_id = version.event_id

    login_local(attacker, "8" * 64)
    resp2 = attacker.post(f"/history/revert-id/revisions/{event_id}/revert", allow_redirects=False)
    assert resp2.status_code in (403, 404)

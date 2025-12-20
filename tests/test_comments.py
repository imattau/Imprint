import asyncio
import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.auth.schemas import SessionData, SessionMode
from app.db import models
from app.db.session import get_session
from app.main import app


def make_client() -> TestClient:
    return TestClient(app)


async def _seed_essay(event_id: str = "root123") -> models.EssayVersion:
    async with get_session() as session:
        essay = models.Essay(identifier="essay-1", author_pubkey="a" * 64, latest_event_id=event_id)
        version = models.EssayVersion(
            essay=essay,
            version=1,
            content="test",
            summary="",
            status="published",
            event_id=event_id,
        )
        session.add_all([essay, version])
        await session.commit()
        return version


class DummySigner:
    def __init__(self, pubkey: str):
        self.pubkey = pubkey

    def get_public_key(self) -> str:
        return self.pubkey

    async def sign_event(self, event: dict) -> dict:
        event["sig"] = "sig"
        return event


@patch("app.comments.service.relay_client.fetch_events", new_callable=AsyncMock)
def test_fetch_comments_threading(mock_fetch):
    root_id = "root123"
    asyncio.run(_seed_essay(root_id))
    mock_fetch.side_effect = [
        [
            {"id": "c1", "pubkey": "a" * 64, "created_at": 1, "tags": [["e", root_id, "", "root"]], "content": "root"},
            {
                "id": "c2",
                "pubkey": "b" * 64,
                "created_at": 2,
                "tags": [["e", root_id, "", "root"], ["e", "c1", "", "reply"]],
                "content": "reply",
            },
        ],
        [],
    ]
    client = make_client()
    with client:
        resp = client.get(f"/posts/{root_id}/comments/list")
    assert resp.status_code == 200
    assert "reply" in resp.text


@patch("app.comments.service.relay_client.publish_event", new_callable=AsyncMock)
@patch("app.nostr.signers.signer_from_session")
@patch("app.comments.service.relay_client.fetch_events", new_callable=AsyncMock, return_value=[])
def test_post_comment_returns_html(mock_fetch, mock_signer, mock_publish):
    root_id = "root123"
    asyncio.run(_seed_essay(root_id))
    mock_signer.return_value = DummySigner("a" * 64)

    session_data = SessionData(session_mode=SessionMode.local, pubkey_hex="a" * 64)
    with patch("app.main.require_signing_session", return_value=session_data):
        client = make_client()
        resp = client.post(f"/posts/{root_id}/comments", data={"content": "hello world"}, allow_redirects=False)
    assert resp.status_code == 200
    assert "hello world" in resp.text
    assert "1 comment" in resp.text
    mock_publish.assert_awaited()


@patch("app.comments.service.relay_client.fetch_events", new_callable=AsyncMock, return_value=[])
def test_local_cache_used_when_relays_empty(mock_fetch):
    root_id = "root123"
    asyncio.run(_seed_essay(root_id))
    # Pre-seed local cache table
    async def seed_cache():
        async with get_session() as session:
            await session.execute(models.CommentCache.__table__.delete())
            existing = await session.execute(
                models.CommentCache.__table__.delete().where(models.CommentCache.event_id == "cid1")
            )
            row = models.CommentCache(
                root_id=root_id,
                event_id="cid1",
                event_json=json.dumps({"id": "cid1", "pubkey": "x" * 64, "content": "cached", "created_at": 1, "tags": [["e", root_id, "", "root"]]}),
            )
            session.add(row)
            await session.commit()
    asyncio.run(seed_cache())
    client = make_client()
    resp = client.get(f"/posts/{root_id}/comments/list")
    assert resp.status_code == 200
    assert "cached" in resp.text

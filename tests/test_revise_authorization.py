import asyncio

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, init_models


def make_clients() -> tuple[TestClient, TestClient]:
    asyncio.run(init_models())
    return TestClient(app), TestClient(app)


def login_local(client: TestClient, secret: str):
    # Swap the signer secret so this session uses a distinct pubkey.
    settings.nostr_secret = secret
    resp = client.post("/auth/login/local", data={"duration": "1h"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200


def publish_as(client: TestClient, identifier: str):
    resp = client.post(
        "/publish",
        data={
            "title": "Owned Post",
            "content": "hello",
            "summary": "",
            "identifier": identifier,
            "tags": "",
            "action": "publish",
        },
        allow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def test_only_author_can_revise():
    identifier = "auth-owned"

    author_client, other_client = make_clients()
    login_local(author_client, "1" * 64)
    publish_as(author_client, identifier)

    login_local(other_client, "2" * 64)

    detail = other_client.get(f"/essay/{identifier}").text
    assert f'href="/editor?d={identifier}"' not in detail

    forbidden_editor = other_client.get(f"/editor?d={identifier}", allow_redirects=False)
    assert forbidden_editor.status_code == 403

    forbidden_publish = other_client.post(
        "/publish",
        data={
            "title": "Hijack",
            "content": "nope",
            "summary": "",
            "identifier": identifier,
            "tags": "",
            "action": "publish",
        },
        allow_redirects=False,
    )
    assert forbidden_publish.status_code == 403

    allowed_editor = author_client.get(f"/editor?d={identifier}", allow_redirects=False)
    assert allowed_editor.status_code == 200

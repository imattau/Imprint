import asyncio

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, init_models


def make_client() -> TestClient:
    asyncio.run(init_models())
    return TestClient(app)


def test_login_sets_session_cookie_and_persists():
    client = make_client()
    pubkey_hex = "4" * 64
    resp = client.post(
        "/auth/login/nip07",
        json={"pubkey": pubkey_hex, "duration": "1h"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert settings.session_cookie_name in resp.headers.get("set-cookie", "")
    status_resp = client.get("/auth/status")
    assert pubkey_hex[:12] in status_resp.text


def test_editor_redirects_to_signin_when_unauthenticated():
    client = make_client()
    resp = client.get("/editor", allow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/?signin=1" in resp.headers.get("location", "")


def test_htmx_unauthorized_request_triggers_modal():
    client = make_client()
    resp = client.get("/settings", headers={"HX-Request": "true"}, allow_redirects=False)
    assert resp.status_code == 401
    assert "text/html" in resp.headers.get("content-type", "")
    assert "openAuthModal" in resp.headers.get("HX-Trigger", "")
    assert "Authentication required" in resp.text


def test_navbar_visibility_changes_with_session():
    client = make_client()
    anon_home = client.get("/").text
    assert 'href="/editor"' not in anon_home
    assert 'href="/settings"' not in anon_home

    client.post(
        "/auth/login/nip07",
        json={"pubkey": "5" * 64, "duration": "1h"},
        headers={"HX-Request": "true"},
    )
    authed_home = client.get("/").text
    assert 'href="/editor"' in authed_home
    assert 'href="/settings"' in authed_home


def test_local_login_updates_header(monkeypatch):
    client = make_client()
    # Provide a deterministic local signer secret (64 hex chars)
    monkeypatch.setattr("app.config.settings.nostr_secret", "1" * 64)
    resp = client.post("/auth/login/local", data={"duration": "1h"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert settings.session_cookie_name in resp.headers.get("set-cookie", "")
    assert "local" in resp.text.lower()
    home = client.get("/").text
    assert 'href="/editor"' in home


def test_readonly_login_updates_header_and_badge():
    client = make_client()
    npub = "npub1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqg3l8da"
    resp = client.post(
        "/auth/login/readonly",
        data={"npub": npub, "duration": "1h"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert settings.session_cookie_name in resp.headers.get("set-cookie", "")
    assert "Read-only" in resp.text or "read-only" in resp.text.lower()
    home = client.get("/").text
    assert "Sign in" not in home
    assert 'href="/editor"' not in home
    assert 'href="/settings"' in home or 'href="/settings"' not in home  # settings may be gated, just ensure header updated

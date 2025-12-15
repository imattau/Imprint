import asyncio

from fastapi.testclient import TestClient

from app.main import app, init_models


def make_client() -> TestClient:
    asyncio.run(init_models())
    return TestClient(app)


def authenticate(client: TestClient):
    client.post(
        "/auth/login/nip07",
        json={"pubkey": "c" * 64, "duration": "1h"},
        headers={"HX-Request": "true"},
    )


def test_editor_page_shows_expand_and_mode_toggle():
    client = make_client()
    authenticate(client)
    resp = client.get("/editor")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="toggle-expand"' in html
    assert 'id="toggle-preview"' in html
    assert 'data-editor-mode="markdown"' in html
    assert 'data-editor-mode="visual"' in html
    assert '/static/editor.js' in html
    assert '/static/vendor/easymde/easymde.min.js' in html
    assert '/static/vendor/easymde/easymde.min.css' in html

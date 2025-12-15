from fastapi.testclient import TestClient

from app.main import app
from app.config import settings


def test_session_cookie_not_secure_in_debug(monkeypatch):
    # Ensure HTTPS-only is disabled to simulate dev/local usage.
    monkeypatch.setattr(settings, "session_cookie_https_only", False)
    client = TestClient(app)
    resp = client.get("/")
    set_cookie = resp.headers.get("set-cookie", "")
    # No secure flag expected when https_only is False
    assert "Secure" not in set_cookie

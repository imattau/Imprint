import asyncio

from fastapi.testclient import TestClient

from app import main
from app.admin.service import InstanceSettingsService
from app.db.session import get_session


def _set_accent(color: str):
    async def _set():
        async with get_session() as session:
            service = InstanceSettingsService(session)
            settings = await service.get_settings()
            settings.theme_accent = color
            await session.commit()
    asyncio.run(_set())


def test_accent_variable_rendered():
    _set_accent("#ff0066")
    client = TestClient(main.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "--accent: #ff0066" in resp.text


def test_accent_fallback_default():
    _set_accent(None)  # type: ignore[arg-type]
    client = TestClient(main.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "--accent: #2f6f73" in resp.text

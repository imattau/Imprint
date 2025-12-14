import pytest
from ecdsa import SigningKey, SECP256k1

from app.services.essays import EssayService
from app.db import models
from app.nostr.key import derive_pubkey_hex


@pytest.mark.asyncio
async def test_next_version_and_publish(session, monkeypatch):
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey = derive_pubkey_hex(sk)

    monkeypatch.setattr("app.services.essays.load_private_key", lambda *_, **__: sk)
    monkeypatch.setattr("app.services.essays.derive_pubkey_hex", lambda *_: pubkey)
    monkeypatch.setattr("app.services.essays.publish_event", lambda *_, **__: None)
    monkeypatch.setattr("app.services.essays.settings", type("S", (), {"relay_urls": [], "nostr_secret": ""}))

    service = EssayService(session)
    version1 = await service.publish(None, "Title", "Body of essay" * 5, "Summary")
    assert version1.version == 1
    version2 = await service.publish(version1.essay.identifier, "Title", "Body update" * 5, "Summary")
    assert version2.version == 2
    assert version2.supersedes_event_id == version1.event_id


@pytest.mark.asyncio
async def test_save_draft_increments(session, monkeypatch):
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey = derive_pubkey_hex(sk)
    monkeypatch.setattr("app.services.essays.load_private_key", lambda *_, **__: sk)
    monkeypatch.setattr("app.services.essays.derive_pubkey_hex", lambda *_: pubkey)
    monkeypatch.setattr("app.services.essays.settings", type("S", (), {"relay_urls": [], "nostr_secret": ""}))

    service = EssayService(session)
    draft1 = await service.save_draft(None, "Title", "Draft content" * 5, None)
    draft2 = await service.save_draft(draft1.essay.identifier, "Title", "Draft content" * 5, None)
    assert draft2.version == draft1.version + 1

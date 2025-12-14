import pytest
from ecdsa import SigningKey, SECP256k1

from app.services.essays import EssayService
from app.db import models
from app.nostr.key import derive_pubkey_hex
from app.nostr.event import build_long_form_event_template, sign_event


@pytest.mark.asyncio
async def test_next_version_and_publish(session, monkeypatch):
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey = derive_pubkey_hex(sk)

    monkeypatch.setattr("app.services.essays.publish_event", lambda *_, **__: None)
    monkeypatch.setattr("app.services.essays.settings", type("S", (), {"relay_urls": [], "nostr_secret": ""}))

    service = EssayService(session)
    essay, version_num, supersedes = await service.prepare_publication(None, "Title", "Summary", pubkey)
    template = build_long_form_event_template(pubkey, essay.identifier, "Title", "Body of essay" * 5, "Summary", version_num, "published", supersedes, [])
    signed = sign_event(sk, template)
    version1 = await service.publish(essay.identifier, "Title", "Body of essay" * 5, "Summary", [], signed, prepared=(essay, version_num, supersedes))
    essay2, version_num2, supersedes2 = await service.prepare_publication(version1.essay.identifier, "Title", "Summary", pubkey)
    template2 = build_long_form_event_template(pubkey, essay2.identifier, "Title", "Body update" * 5, "Summary", version_num2, "published", supersedes2, [])
    signed2 = sign_event(sk, template2)
    version2 = await service.publish(version1.essay.identifier, "Title", "Body update" * 5, "Summary", [], signed2, prepared=(essay2, version_num2, supersedes2))
    assert version2.version == 2
    assert version2.supersedes_event_id == version1.event_id


@pytest.mark.asyncio
async def test_save_draft_increments(session, monkeypatch):
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey = derive_pubkey_hex(sk)
    monkeypatch.setattr("app.services.essays.settings", type("S", (), {"relay_urls": [], "nostr_secret": ""}))

    service = EssayService(session)
    draft1 = await service.save_draft(None, "Title", "Draft content" * 5, None, author_pubkey=pubkey)
    draft2 = await service.save_draft(draft1.essay.identifier, "Title", "Draft content" * 5, None, author_pubkey=pubkey)
    assert draft2.version == draft1.version + 1

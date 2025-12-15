import time
from ecdsa import SigningKey, SECP256k1

from app.nostr.event import build_long_form_event, verify_event, ensure_imprint_tag, IMPRINT_TAG
from app.nostr.key import derive_pubkey_hex


def test_event_signing_and_verification():
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey = derive_pubkey_hex(sk)
    event = build_long_form_event(
        sk=sk,
        pubkey=pubkey,
        identifier="test-1",
        title="Hello",
        content="content body",
        summary="summary",
        version=1,
        status="published",
    )
    assert verify_event(event)


def test_imprint_tag_always_added():
    topics = ["nostr"]
    ensured = ensure_imprint_tag(topics)
    assert IMPRINT_TAG in ensured
    assert len([t for t in ensured if t == IMPRINT_TAG]) == 1
    # ensure original topics preserved
    assert "nostr" in ensured

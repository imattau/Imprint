import time
from ecdsa import SigningKey, SECP256k1

from app.nostr.event import build_long_form_event, verify_event
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

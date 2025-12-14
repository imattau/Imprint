import json
import asyncio

import pytest
from ecdsa import SigningKey, SECP256k1
from fastapi.testclient import TestClient

from app.main import app, init_models
from app.nostr.event import sign_event, build_long_form_event_template
from app.nostr.key import encode_npub, derive_pubkey_hex


def client_with_session():
    asyncio.run(init_models())
    return TestClient(app)


def test_readonly_login_blocks_publish():
    client = client_with_session()
    pubkey_hex = "1" * 64
    npub = encode_npub(pubkey_hex)
    resp = client.post(
        "/auth/login/readonly",
        data={"npub": npub, "duration": "1h"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    publish = client.post(
        "/publish",
        data={"title": "Test", "content": "body", "summary": "", "identifier": "t1", "tags": "", "action": "publish"},
        allow_redirects=False,
    )
    assert publish.status_code == 403


def test_nip07_login_accepts_pubkey():
    client = client_with_session()
    pubkey_hex = "2" * 64
    resp = client.post(
        "/auth/login/nip07",
        json={"pubkey": pubkey_hex, "duration": "1h"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    publish = client.post(
        "/publish",
        data={"title": "NIP07", "content": "body", "identifier": "n7", "tags": "", "action": "publish"},
        allow_redirects=False,
    )
    assert publish.status_code == 400


def test_nip46_session_created():
    client = client_with_session()
    signer_hex = "3" * 64
    npub = encode_npub(signer_hex)
    resp = client.post(
        "/auth/login/nip46",
        data={"signer_pubkey": npub, "relay": "wss://relay.example", "duration": "15m"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    status_html = resp.text
    assert "relay.example" in status_html


def test_signed_event_validation_endpoint():
    client = client_with_session()
    sk = SigningKey.generate(curve=SECP256k1)
    pubkey_hex = derive_pubkey_hex(sk)
    client.post("/auth/login/nip07", json={"pubkey": pubkey_hex, "duration": "1h"}, headers={"HX-Request": "true"})
    template = build_long_form_event_template(pubkey_hex, "demo", "Title", "Content", None, 1, "published", None, [])
    signed = sign_event(sk, template)
    resp = client.post("/auth/nip07/sign", json=signed)
    assert resp.status_code == 200
    tampered = json.loads(json.dumps(signed))
    tampered["id"] = "0" * 64
    bad = client.post("/auth/nip07/sign", json=tampered)
    assert bad.status_code == 400

import hashlib
import json
import time
from typing import Any, Dict, List, Optional

from ecdsa import SigningKey, VerifyingKey, SECP256k1, BadSignatureError


class NostrEventError(Exception):
    pass


IMPRINT_TAG = "imprint"


def ensure_imprint_tag(topics: Optional[list[str]] = None) -> list[str]:
    """Guarantee the canonical Imprint tag is present exactly once."""
    deduped: list[str] = []
    for topic in topics or []:
        if topic and topic not in deduped:
            deduped.append(topic)
    if IMPRINT_TAG not in deduped:
        deduped.append(IMPRINT_TAG)
    return deduped


def serialize_event(pubkey: str, created_at: int, kind: int, tags: List[List[str]], content: str) -> str:
    data = [0, pubkey, created_at, kind, tags, content]
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def compute_event_id(serialized_event: str) -> str:
    return hashlib.sha256(serialized_event.encode("utf-8")).hexdigest()


def sign_event(sk: SigningKey, event: Dict[str, Any]) -> Dict[str, Any]:
    serialized = serialize_event(event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"])
    event_id = compute_event_id(serialized)
    signature = sk.sign_digest(bytes.fromhex(event_id)).hex()
    event["id"] = event_id
    event["sig"] = signature
    return event


def build_long_form_event_template(
    pubkey: str,
    identifier: str,
    title: str,
    content: str,
    summary: Optional[str],
    version: int,
    status: str,
    supersedes: Optional[str] = None,
    topics: Optional[list[str]] = None,
) -> Dict[str, Any]:
    created_at = int(time.time())
    tags: List[List[str]] = [
        ["d", identifier],
        ["title", title],
        ["published_at", str(created_at)],
        ["version", str(version)],
        ["status", status],
    ]
    if summary:
        tags.append(["summary", summary])
    if supersedes:
        tags.append(["supersedes", supersedes])
    for topic in ensure_imprint_tag(topics):
        tags.append(["t", topic])
    return {
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": 30023,
        "tags": tags,
        "content": content,
    }


def verify_event(event: Dict[str, Any]) -> bool:
    try:
        serialized = serialize_event(event["pubkey"], event["created_at"], event["kind"], event.get("tags", []), event.get("content", ""))
        event_id = compute_event_id(serialized)
        if event_id != event.get("id"):
            return False
        vk = VerifyingKey.from_string(bytes.fromhex(event["pubkey"]), curve=SECP256k1)
        vk.verify_digest(bytes.fromhex(event["sig"]), bytes.fromhex(event_id))
        return True
    except (BadSignatureError, KeyError, ValueError):
        return False


def build_long_form_event(
    sk: SigningKey,
    pubkey: str,
    identifier: str,
    title: str,
    content: str,
    summary: Optional[str],
    version: int,
    status: str,
    supersedes: Optional[str] = None,
    topics: Optional[list[str]] = None,
) -> Dict[str, Any]:
    created_at = int(time.time())
    tags: List[List[str]] = [
        ["d", identifier],
        ["title", title],
        ["published_at", str(created_at)],
        ["version", str(version)],
        ["status", status],
    ]
    if summary:
        tags.append(["summary", summary])
    if supersedes:
        tags.append(["supersedes", supersedes])
    for topic in ensure_imprint_tag(topics):
        tags.append(["t", topic])
    event = {
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": 30023,
        "tags": tags,
        "content": content,
    }
    return sign_event(sk, event)

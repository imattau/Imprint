from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, Iterable, Optional, Sequence

from app.auth.schemas import SessionData
from app.nostr.event import verify_event
from app.nostr.relay_client import relay_client
from app.nostr.signers import signer_from_session, SignerError

# In-memory cache primarily for test/dev; relay fetch will augment when available.
_likes: Dict[str, set[str]] = defaultdict(set)
_zaps: Dict[str, Dict[str, int]] = defaultdict(lambda: {"count": 0, "sats": 0})
_engagement_cache: Dict[str, tuple[float, dict[str, dict]]] = {}
_CACHE_TTL_SECONDS = 20
_MAX_RELAYS = 5


def _should_skip_network() -> bool:
    # Avoid network during tests or when explicitly disabled.
    return bool(__import__("os").getenv("PYTEST_CURRENT_TEST"))


def _cache_key(event_ids: Iterable[str], viewer: Optional[SessionData]) -> str:
    viewer_key = viewer.npub if viewer else "anon"
    return f"{','.join(sorted(set(event_ids)))}|{viewer_key}"


def _get_cached(key: str) -> Optional[dict[str, dict]]:
    expires_data = _engagement_cache.get(key)
    if not expires_data:
        return None
    expires_at, payload = expires_data
    if expires_at < time.time():
        _engagement_cache.pop(key, None)
        return None
    return payload


def _set_cached(key: str, payload: dict[str, dict]) -> None:
    _engagement_cache[key] = (time.time() + _CACHE_TTL_SECONDS, payload)


def _invalidate_cache(event_id: Optional[str] = None) -> None:
    if event_id is None:
        _engagement_cache.clear()
        return
    for key in list(_engagement_cache.keys()):
        if event_id in key.split("|", 1)[0].split(","):
            _engagement_cache.pop(key, None)


def engagement_for(event_id: str, viewer: Optional[SessionData]) -> dict:
    viewer_npub = viewer.npub if viewer else None
    liked = viewer_npub in _likes[event_id] if viewer_npub else False
    return {
        "event_id": event_id,
        "like_count": len(_likes[event_id]),
        "liked_by_me": liked,
        "zap_count": _zaps[event_id]["count"],
        "total_sats": _zaps[event_id]["sats"],
    }


async def publish_reaction(event_id: str, author_pubkey: str, session: SessionData, relays: Sequence[str]) -> None:
    """Publish a NIP-25 reaction event to configured relays."""

    try:
        signer = signer_from_session(session)
    except SignerError:
        return
    event = {
        "pubkey": session.pubkey_hex or "",
        "created_at": int(time.time()),
        "kind": 7,
        "tags": [["e", event_id], ["p", author_pubkey]],
        "content": "+",
    }
    try:
        signed = await signer.sign_event(event)
    except SignerError:
        # Browser signer must sign client-side; skip server publish.
        return
    if _should_skip_network():
        return
    await relay_client.publish_event(signed, relays)


async def publish_zap_request(event_id: str, author_pubkey: str, session: SessionData, relays: Sequence[str]) -> None:
    """Publish a NIP-57 zap request skeleton to relays (amount handled by client wallet)."""

    try:
        signer = signer_from_session(session)
    except SignerError:
        return
    event = {
        "pubkey": session.pubkey_hex or "",
        "created_at": int(time.time()),
        "kind": 9734,
        "tags": [["e", event_id], ["p", author_pubkey]],
        "content": "",
    }
    try:
        signed = await signer.sign_event(event)
    except SignerError:
        return
    if _should_skip_network():
        return
    await relay_client.publish_event(signed, relays)


async def toggle_like(event_id: str, author_pubkey: str, viewer: SessionData, relays: Sequence[str]) -> dict:
    if viewer.npub in _likes[event_id]:
        _likes[event_id].remove(viewer.npub)
    else:
        _likes[event_id].add(viewer.npub)
        await publish_reaction(event_id, author_pubkey, viewer, relays)
    _invalidate_cache(event_id)
    return engagement_for(event_id, viewer)


async def add_zap(event_id: str, sats: int, author_pubkey: str, viewer: SessionData, relays: Sequence[str]) -> dict:
    # Keep zap counts authoritative from receipts; only publish the request.
    await publish_zap_request(event_id, author_pubkey, viewer, relays)
    _invalidate_cache(event_id)
    return engagement_for(event_id, viewer)


async def hydrate_from_relays(event_ids: Sequence[str], relays: Sequence[str]) -> None:
    """Fetch reactions and zap receipts for events and prime caches. Best-effort."""

    ids = list(dict.fromkeys(event_ids))
    if not ids or _should_skip_network():
        return

    existing_likes = {eid: set(_likes[eid]) for eid in ids}
    # Reset counters so repeated hydrations don't double count; merge optimistic likes back after fetch.
    for eid in ids:
        _likes[eid] = set()
        _zaps[eid] = {"count": 0, "sats": 0}

    filters = [{"kinds": [7, 9735], "#e": ids}]
    trimmed_relays = list(dict.fromkeys(relays))[:_MAX_RELAYS]
    events = await relay_client.fetch_events(filters, trimmed_relays)
    for event in events:
        if not verify_event(event):
            continue
        kind = event.get("kind")
        tags = event.get("tags") or []
        target_ids = []
        for tag in tags:
            if tag and tag[0] == "e" and len(tag) > 1:
                target_ids.append(tag[1])
        # If no explicit e tag, fall back to the requested filters.
        target_ids = target_ids or ids
        for eid in target_ids:
            if eid not in ids:
                continue
            if kind == 7:
                _likes[eid].add(event.get("pubkey", ""))
            elif kind == 9735:
                _zaps[eid]["count"] += 1
                amount = 0
                for tag in tags:
                    if tag and tag[0] == "amount" and len(tag) > 1:
                        try:
                            amount = int(tag[1]) // 1000  # msats to sats
                        except ValueError:
                            amount = 0
                        break
                _zaps[eid]["sats"] += max(amount, 0)

    for eid in ids:
        _likes[eid].update(existing_likes.get(eid, set()))


async def engagements_for(event_ids: Sequence[str], viewer: Optional[SessionData], relays: Sequence[str]) -> dict[str, dict]:
    ids = [eid for eid in dict.fromkeys(event_ids) if eid]
    key = _cache_key(ids, viewer)
    cached = _get_cached(key)
    if cached is not None:
        return cached

    await hydrate_from_relays(ids, relays)
    payload = {eid: engagement_for(eid, viewer) for eid in ids}
    _set_cached(key, payload)
    return payload

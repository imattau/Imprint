from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Dict, Optional, Sequence

from app.auth.schemas import SessionData, SessionMode
from app.nostr.event import verify_event
from app.nostr.relay import publish_event as relay_publish
from app.nostr.signers import signer_from_session, SignerError

# In-memory cache primarily for test/dev; relay fetch will augment when available.
_likes: Dict[str, set[str]] = defaultdict(set)
_zaps: Dict[str, Dict[str, int]] = defaultdict(lambda: {"count": 0, "sats": 0})


def _should_skip_network() -> bool:
    # Avoid network during tests or when explicitly disabled.
    return bool(__import__("os").getenv("PYTEST_CURRENT_TEST"))


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
    await asyncio.gather(*(relay_publish(relay, signed) for relay in relays))


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
    await asyncio.gather(*(relay_publish(relay, signed) for relay in relays))


async def toggle_like(event_id: str, author_pubkey: str, viewer: SessionData, relays: Sequence[str]) -> dict:
    if viewer.npub in _likes[event_id]:
        _likes[event_id].remove(viewer.npub)
    else:
        _likes[event_id].add(viewer.npub)
        await publish_reaction(event_id, author_pubkey, viewer, relays)
    return engagement_for(event_id, viewer)


async def add_zap(event_id: str, sats: int, author_pubkey: str, viewer: SessionData, relays: Sequence[str]) -> dict:
    _zaps[event_id]["count"] += 1
    _zaps[event_id]["sats"] += max(sats, 0)
    await publish_zap_request(event_id, author_pubkey, viewer, relays)
    return engagement_for(event_id, viewer)


async def hydrate_from_relays(event_id: str, relays: Sequence[str]) -> None:
    """Fetch reactions and zap receipts for an event and prime caches. Best-effort."""

    if _should_skip_network():
        return
    import websockets  # lazy import to avoid dependency when unused

    filters = {"kinds": [7, 9735], "#e": [event_id]}
    for relay in relays:
        try:
            async with websockets.connect(relay) as ws:
                sub_id = f"engage-{event_id[:6]}"
                await ws.send(json.dumps(["REQ", sub_id, filters]))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg[0] == "EOSE":
                        break
                    if msg[0] != "EVENT" or len(msg) < 3:
                        continue
                    event = msg[2]
                    if not verify_event(event):
                        continue
                    kind = event.get("kind")
                    tags = event.get("tags") or []
                    if kind == 7:
                        _likes[event_id].add(event.get("pubkey", ""))
                    elif kind == 9735:
                        _zaps[event_id]["count"] += 1
                        amount = 0
                        for tag in tags:
                            if tag and tag[0] == "amount" and len(tag) > 1:
                                try:
                                    amount = int(tag[1]) // 1000  # msats to sats
                                except ValueError:
                                    amount = 0
                                break
                        _zaps[event_id]["sats"] += max(amount, 0)
        except Exception:
            continue

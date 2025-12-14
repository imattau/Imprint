import asyncio
import json
from typing import Any, AsyncGenerator, Dict

import websockets

from app.nostr.event import verify_event


class RelayError(Exception):
    pass


async def publish_event(relay_url: str, event: Dict[str, Any]) -> None:
    try:
        async with websockets.connect(relay_url) as ws:
            await ws.send(json.dumps(["EVENT", event]))
            try:
                await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                pass
    except Exception as exc:  # noqa: BLE001
        raise RelayError(f"Failed to publish to relay {relay_url}: {exc}") from exc


aSYNC_SUB_FILTER = {
    "kinds": [30023],
}

async def subscribe_long_form(relay_url: str, since: int | None = None, limit: int = 50) -> AsyncGenerator[Dict[str, Any], None]:
    req = {"kinds": [30023], "limit": limit}
    if since:
        req["since"] = since
    sub_id = "imprint-feed"
    try:
        async with websockets.connect(relay_url) as ws:
            await ws.send(json.dumps(["REQ", sub_id, req]))
            async for message in ws:
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if len(msg) < 2:
                    continue
                if msg[0] == "EVENT" and len(msg) >= 3:
                    event = msg[2]
                    if verify_event(event):
                        yield event
                if msg[0] == "EOSE":
                    break
    except Exception as exc:  # noqa: BLE001
        raise RelayError(f"Failed to subscribe to relay {relay_url}: {exc}") from exc

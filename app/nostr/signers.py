import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import websockets

from app.auth.schemas import SessionData, SessionMode
from app.nostr.event import compute_event_id, serialize_event, sign_event as nostr_sign_event
from app.nostr.key import load_private_key, derive_pubkey_hex
from app.config import settings


class SignerError(Exception):
    pass


class BaseSigner:
    def get_public_key(self) -> str:
        raise NotImplementedError

    async def sign_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class LocalSigner(BaseSigner):
    def __init__(self, secret: Optional[str] = None):
        self._sk = load_private_key(secret or settings.nostr_secret)
        self._pubkey = derive_pubkey_hex(self._sk)

    def get_public_key(self) -> str:
        return self._pubkey

    async def sign_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event["pubkey"] = self._pubkey
        return nostr_sign_event(self._sk, event)


class Nip07SignerAdapter(BaseSigner):
    def __init__(self, pubkey_hex: str):
        self._pubkey = pubkey_hex

    def get_public_key(self) -> str:
        return self._pubkey

    async def sign_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        raise SignerError("Browser signer must sign in the client")


@dataclass
class Nip46Session:
    client_secret: str
    signer_pubkey: str
    relay: str


class Nip46Transport:
    def __init__(self, relay_url: str):
        self.relay_url = relay_url

    async def send_request(self, message: Dict[str, Any]) -> Dict[str, Any]:
        async with websockets.connect(self.relay_url) as ws:
            await ws.send(json.dumps(message))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            return json.loads(raw)


class Nip46Signer(BaseSigner):
    def __init__(self, session: Nip46Session, transport: Optional[Nip46Transport] = None):
        self.session = session
        self.transport = transport or Nip46Transport(session.relay)

    def get_public_key(self) -> str:
        return self.session.signer_pubkey

    async def sign_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        request = {
            "method": "sign_event",
            "params": [event],
            "id": compute_event_id(serialize_event(event.get("pubkey"), event.get("created_at"), event.get("kind"), event.get("tags", []), event.get("content", ""))),
        }
        try:
            response = await self.transport.send_request(request)
        except Exception as exc:  # pragma: no cover - network
            raise SignerError("Remote signer unavailable") from exc
        result = response.get("result") if isinstance(response, dict) else None
        if not result:
            raise SignerError("Remote signer error")
        return result


def signer_from_session(session: SessionData) -> BaseSigner:
    if session.session_mode == SessionMode.local:
        return LocalSigner()
    if session.session_mode == SessionMode.nip07 and session.pubkey_hex:
        return Nip07SignerAdapter(session.pubkey_hex)
    if session.session_mode == SessionMode.nip46 and session.signer_pubkey and session.client_secret and session.relay:
        nip46_session = Nip46Session(
            client_secret=session.client_secret,
            signer_pubkey=session.signer_pubkey,
            relay=session.relay,
        )
        return Nip46Signer(nip46_session)
    raise SignerError("Unsupported session")


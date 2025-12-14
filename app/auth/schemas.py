from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SessionMode(str, Enum):
    readonly = "readonly"
    nip07 = "nip07"
    nip46 = "nip46"
    local = "local"


class SessionData(BaseModel):
    session_mode: SessionMode
    pubkey_hex: Optional[str] = None
    npub: Optional[str] = None
    display_name: Optional[str] = None
    expires_at: Optional[dt.datetime] = Field(default=None, description="UTC expiration timestamp")
    signer_pubkey: Optional[str] = None
    relay: Optional[str] = None
    client_secret: Optional[str] = None

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return dt.datetime.now(dt.timezone.utc) >= self.expires_at


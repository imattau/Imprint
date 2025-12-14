from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.nostr.key import NostrKeyError, decode_nip19


ADDRESS_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+$")
HEX_COLOR_PATTERN = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _trim(value: Optional[str], max_length: int) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:max_length]


class InstanceSettingsPayload(BaseModel):
    site_name: str = Field(default="Imprint", max_length=120)
    site_tagline: Optional[str] = Field(default=None, max_length=255)
    site_description: Optional[str] = Field(default=None, max_length=2000)
    public_base_url: Optional[str] = Field(default=None, max_length=255)
    default_relays: Optional[str] = Field(default=None, max_length=1000)
    instance_nostr_address: Optional[str] = Field(default=None, max_length=255)
    instance_admin_npub: Optional[str] = Field(default=None, max_length=128)
    lightning_address: Optional[str] = Field(default=None, max_length=255)
    donation_message: Optional[str] = Field(default=None, max_length=255)
    enable_payments: bool = False
    enable_public_essays_feed: bool = True
    enable_registrationless_readonly: bool = True
    max_feed_items: int = 15
    session_default_minutes: int = 60
    theme_accent: Optional[str] = Field(default=None, max_length=16)

    @field_validator(
        "site_name",
        "site_tagline",
        "site_description",
        "public_base_url",
        "default_relays",
        "instance_nostr_address",
        "instance_admin_npub",
        "lightning_address",
        "donation_message",
        "theme_accent",
        mode="before",
    )
    @classmethod
    def clean_text(cls, value, info):  # type: ignore[override]
        field_name = info.field_name or ""
        max_length = 255
        if field_name in cls.model_fields:
            for meta in cls.model_fields[field_name].metadata:
                if hasattr(meta, "max_length") and meta.max_length:
                    max_length = meta.max_length
                    break
        return _trim(value, max_length)

    @field_validator("public_base_url")
    @classmethod
    def validate_base_url(cls, value: Optional[str]):
        if not value:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Public base URL must include http or https")
        return value

    @field_validator("default_relays")
    @classmethod
    def normalize_relays(cls, value: Optional[str]):
        if not value:
            return None
        relays = []
        for raw in value.split(","):
            relay = raw.strip()
            if not relay:
                continue
            parsed = urlparse(relay)
            if parsed.scheme not in {"ws", "wss", "http", "https"}:
                raise ValueError("Relays must include a ws/wss/http/https scheme")
            relays.append(relay)
        return ",".join(dict.fromkeys(relays)) if relays else None

    @field_validator("instance_nostr_address", "lightning_address")
    @classmethod
    def validate_address(cls, value: Optional[str]):
        if not value:
            return None
        if not ADDRESS_PATTERN.match(value):
            raise ValueError("Must be in name@domain format")
        return value

    @field_validator("instance_admin_npub")
    @classmethod
    def validate_npub(cls, value: Optional[str]):
        if not value:
            return None
        try:
            decode_nip19(value)
        except NostrKeyError as exc:
            raise ValueError("Invalid npub format") from exc
        return value

    @field_validator("max_feed_items")
    @classmethod
    def validate_feed_limit(cls, value: int):
        if value < 1 or value > 100:
            raise ValueError("Feed items must be between 1 and 100")
        return value

    @field_validator("session_default_minutes")
    @classmethod
    def validate_session_minutes(cls, value: int):
        if value < 5 or value > 24 * 60:
            raise ValueError("Session default minutes must be between 5 and 1440")
        return value

    @field_validator("theme_accent")
    @classmethod
    def validate_color(cls, value: Optional[str]):
        if not value:
            return None
        if not HEX_COLOR_PATTERN.match(value):
            raise ValueError("Accent must be a 6-digit hex color")
        return value if value.startswith("#") else f"#{value}"

    def relays_list(self) -> list[str]:
        if not self.default_relays:
            return []
        return [relay.strip() for relay in self.default_relays.split(",") if relay.strip()]

from __future__ import annotations

import datetime as dt
import logging
import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import InstanceSettingsPayload
from app.auth.service import get_auth_session
from app.config import settings as app_settings
from app.db import models
from app.nostr.key import NostrKeyError, decode_nip19

logger = logging.getLogger(__name__)


def admin_token() -> Optional[str]:
    token = os.getenv("ADMIN_TOKEN")
    return token.strip() if token else None


def admin_allowlist() -> set[str]:
    # From environment and static settings
    env_list = {npub.strip() for npub in os.getenv("ADMIN_NPUBS", "").split(",") if npub.strip()}
    configured = set(app_settings.admin_npubs or [])
    return {npub for npub in env_list.union(configured) if npub}


def has_allowlisted_pubkey(request: Request) -> bool:
    session = get_auth_session(request)
    if not session or not session.npub:
        return False
    allowlist = set(admin_allowlist())
    instance_settings = getattr(request.state, "instance_settings", None)
    if instance_settings and instance_settings.admin_allowlist:
        allowlist.update({npub.strip() for npub in instance_settings.admin_allowlist.split(",") if npub.strip()})
    return session.npub in allowlist


def issue_admin_session(request: Request) -> None:
    request.session["is_admin"] = True
    request.session.setdefault("admin_csrf", secrets.token_hex(16))


def clear_admin_session(request: Request) -> None:
    request.session.pop("is_admin", None)
    request.session.pop("admin_csrf", None)


def require_admin(request: Request) -> None:
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


def ensure_admin_csrf(request: Request) -> str:
    token = request.session.get("admin_csrf")
    if not token:
        token = secrets.token_hex(16)
        request.session["admin_csrf"] = token
    return token


def validate_admin_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("admin_csrf")
    if not expected or not token or expected != token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid CSRF token")


class InstanceSettingsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _fetch(self) -> Optional[models.InstanceSettings]:
        result = await self.session.execute(select(models.InstanceSettings).limit(1))
        return result.scalars().first()

    async def get_settings(self) -> models.InstanceSettings:
        existing = await self._fetch()
        if existing:
            return existing
        defaults = InstanceSettingsPayload(
            site_name="Imprint",
            default_relays=",".join(app_settings.relay_urls) if app_settings.relay_urls else None,
            max_feed_items=15,
            session_default_minutes=60,
            enable_public_essays_feed=True,
            enable_registrationless_readonly=True,
        )
        return await self.create_settings(defaults)

    async def create_settings(self, payload: InstanceSettingsPayload) -> models.InstanceSettings:
        settings = models.InstanceSettings(
            site_name=payload.site_name,
            site_tagline=payload.site_tagline,
            site_description=payload.site_description,
            public_base_url=payload.public_base_url,
            default_relays=payload.default_relays,
            instance_nostr_address=payload.instance_nostr_address,
            instance_admin_npub=payload.instance_admin_npub,
            instance_admin_pubkey=self._derive_pubkey(payload.instance_admin_npub),
            admin_allowlist=payload.admin_allowlist,
            blocked_pubkeys=payload.blocked_pubkeys,
            lightning_address=payload.lightning_address,
            donation_message=payload.donation_message,
            enable_payments=payload.enable_payments,
            enable_public_essays_feed=payload.enable_public_essays_feed,
            enable_registrationless_readonly=payload.enable_registrationless_readonly,
            filter_recently_published_to_imprint_only=payload.filter_recently_published_to_imprint_only,
            max_feed_items=payload.max_feed_items,
            session_default_minutes=payload.session_default_minutes,
            theme_accent=payload.theme_accent,
            updated_at=dt.datetime.now(dt.timezone.utc),
        )
        self.session.add(settings)
        await self.session.commit()
        await self.session.refresh(settings)
        return settings

    async def update_settings(self, payload: InstanceSettingsPayload, updated_by_pubkey: Optional[str]) -> models.InstanceSettings:
        settings = await self.get_settings()
        settings.site_name = payload.site_name
        settings.site_tagline = payload.site_tagline
        settings.site_description = payload.site_description
        settings.public_base_url = payload.public_base_url
        settings.default_relays = payload.default_relays
        settings.instance_nostr_address = payload.instance_nostr_address
        settings.instance_admin_npub = payload.instance_admin_npub
        settings.instance_admin_pubkey = self._derive_pubkey(payload.instance_admin_npub)
        settings.admin_allowlist = payload.admin_allowlist
        settings.blocked_pubkeys = payload.blocked_pubkeys
        settings.lightning_address = payload.lightning_address
        settings.donation_message = payload.donation_message
        settings.enable_payments = payload.enable_payments
        settings.enable_public_essays_feed = payload.enable_public_essays_feed
        settings.enable_registrationless_readonly = payload.enable_registrationless_readonly
        settings.filter_recently_published_to_imprint_only = payload.filter_recently_published_to_imprint_only
        settings.max_feed_items = payload.max_feed_items
        settings.session_default_minutes = payload.session_default_minutes
        settings.theme_accent = payload.theme_accent
        settings.updated_at = dt.datetime.now(dt.timezone.utc)
        settings.updated_by_pubkey = updated_by_pubkey
        await self.session.commit()
        await self.session.refresh(settings)
        logger.info(
            "Instance settings updated by %s: %s",
            updated_by_pubkey or "admin",
            self._redact_settings(payload),
        )
        return settings

    def _derive_pubkey(self, npub: Optional[str]) -> Optional[str]:
        if not npub:
            return None
        try:
            return decode_nip19(npub)
        except NostrKeyError:
            return None

    def relays_list(self, settings: models.InstanceSettings) -> list[str]:
        if not settings.default_relays:
            return []
        return [relay.strip() for relay in settings.default_relays.split(",") if relay.strip()]

    def _redact_settings(self, payload: InstanceSettingsPayload) -> dict[str, object]:
        return {
            "site_name": payload.site_name,
            "public_base_url": payload.public_base_url,
            "default_relays": payload.default_relays,
            "enable_payments": payload.enable_payments,
            "enable_public_essays_feed": payload.enable_public_essays_feed,
            "enable_registrationless_readonly": payload.enable_registrationless_readonly,
            "filter_recently_published_to_imprint_only": payload.filter_recently_published_to_imprint_only,
            "max_feed_items": payload.max_feed_items,
            "session_default_minutes": payload.session_default_minutes,
            "admin_allowlist": payload.admin_allowlist,
            "blocked_pubkeys": payload.blocked_pubkeys,
        }


def parse_bool(value: str | None) -> bool:
    return value == "on" or value == "true" or value == "1"


def coerce_payload(data: dict[str, str]) -> InstanceSettingsPayload:
    max_feed_val = data.get("max_feed_items")
    session_minutes_val = data.get("session_default_minutes")
    return InstanceSettingsPayload(
        site_name=data.get("site_name") or "Imprint",
        site_tagline=data.get("site_tagline"),
        site_description=data.get("site_description"),
        public_base_url=data.get("public_base_url"),
        default_relays=data.get("default_relays"),
        instance_nostr_address=data.get("instance_nostr_address"),
        instance_admin_npub=data.get("instance_admin_npub"),
        lightning_address=data.get("lightning_address"),
        donation_message=data.get("donation_message"),
        enable_payments=parse_bool(data.get("enable_payments")),
        enable_public_essays_feed=parse_bool(data.get("enable_public_essays_feed")),
        enable_registrationless_readonly=parse_bool(data.get("enable_registrationless_readonly")),
        filter_recently_published_to_imprint_only=parse_bool(
            data.get("filter_recently_published_to_imprint_only")
        ),
        max_feed_items=max_feed_val if max_feed_val not in (None, "") else 15,
        session_default_minutes=session_minutes_val if session_minutes_val not in (None, "") else 60,
        theme_accent=data.get("theme_accent"),
        admin_allowlist=data.get("admin_allowlist"),
        blocked_pubkeys=data.get("blocked_pubkeys"),
    )

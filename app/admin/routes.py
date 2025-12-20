from __future__ import annotations

import logging
import asyncio
import secrets
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import websockets

from app.admin.service import (
    admin_token,
    admin_allowlist,
    clear_admin_session,
    coerce_payload,
    ensure_admin_csrf,
    has_allowlisted_pubkey,
    issue_admin_session,
    require_admin,
    validate_admin_csrf,
    InstanceSettingsService,
)
from app.admin.schemas import InstanceSettingsPayload
from app.admin.backup import create_backup_archive, validate_backup_archive, apply_restore_from_archive
from app.auth.service import get_auth_session
from app.db import models
from app.db.session import get_session
from app.services.admin_events import AdminEventService
from app.template_utils import register_filters


router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
register_filters(templates)
logger = logging.getLogger(__name__)


async def db_session():
    async with get_session() as session:
        yield session


@router.get("/", response_class=HTMLResponse)
async def admin_home(request: Request, session: AsyncSession = Depends(db_session)):
    settings = await InstanceSettingsService(session).get_settings()
    csrf = ensure_admin_csrf(request)
    if not request.session.get("is_admin") and has_allowlisted_pubkey(request):
        auth_session = get_auth_session(request)
        await AdminEventService(session).log_event(
            action="admin_allowlist_login",
            level="info",
            message="Admin session granted via allowlist",
            actor_pubkey=auth_session.pubkey_hex if auth_session else None,
            metadata={
                "npub": getattr(auth_session, "npub", None),
                "pubkey_hex": getattr(auth_session, "pubkey_hex", None),
            },
        )
        logger.info(
            "Admin allowlist auto-login npub=%s pubkey_hex=%s allowlisted=%s",
            getattr(auth_session, "npub", None),
            getattr(auth_session, "pubkey_hex", None),
            True,
        )
        issue_admin_session(request)
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    if not request.session.get("is_admin"):
        auth_session = get_auth_session(request)
        instance_allowlist = []
        if settings and settings.admin_allowlist:
            instance_allowlist = [npub.strip().lower() for npub in settings.admin_allowlist.split(",") if npub.strip()]
        allowlist_values = sorted(set(admin_allowlist()).union(instance_allowlist))
        await AdminEventService(session).log_event(
            action="admin_login_required",
            level="warn",
            message="Admin login required",
            actor_pubkey=auth_session.pubkey_hex if auth_session else None,
            metadata={
                "npub": getattr(auth_session, "npub", None),
                "pubkey_hex": getattr(auth_session, "pubkey_hex", None),
                "allowlisted": has_allowlisted_pubkey(request),
                "allowlist": allowlist_values,
            },
        )
        logger.info(
            "Admin login required npub=%s pubkey_hex=%s allowlisted=%s",
            getattr(auth_session, "npub", None),
            getattr(auth_session, "pubkey_hex", None),
            has_allowlisted_pubkey(request),
        )
        context = {
            "request": request,
            "allowlisted": has_allowlisted_pubkey(request),
            "has_token": bool(admin_token()),
            "error": None,
            "settings": settings,
            "csrf": csrf,
        }
        return templates.TemplateResponse("admin/login.html", context)

    get_auth_session(request)
    return RedirectResponse(url="/admin/overview", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/login")
async def admin_login(
    request: Request,
    session: AsyncSession = Depends(db_session),
    admin_token_input: str = Form(""),
    csrf: str = Form(""),
):
    validate_admin_csrf(request, csrf)
    token_env = admin_token()
    allowlisted = has_allowlisted_pubkey(request)
    if (token_env and admin_token_input.strip() and secrets.compare_digest(admin_token_input.strip(), token_env)) or allowlisted:
        issue_admin_session(request)
        return RedirectResponse(url="/admin/overview", status_code=status.HTTP_303_SEE_OTHER)

    settings_error_context = {
        "request": request,
        "allowlisted": allowlisted,
        "has_token": bool(token_env),
        "error": "Invalid admin token" if admin_token_input else "Admin credentials required",
        "settings": await InstanceSettingsService(session).get_settings(),
        "csrf": ensure_admin_csrf(request),
    }
    return templates.TemplateResponse("admin/login.html", settings_error_context, status_code=status.HTTP_401_UNAUTHORIZED)


@router.post("/logout")
async def admin_logout(request: Request, csrf: str = Form("")):
    validate_admin_csrf(request, csrf)
    clear_admin_session(request)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/overview", response_class=HTMLResponse)
async def admin_overview(request: Request, session: AsyncSession = Depends(db_session)):
    require_admin(request)
    settings = await InstanceSettingsService(session).get_settings()
    auth_session = get_auth_session(request)
    context = {
        "request": request,
        "settings": settings,
        "auth_session": auth_session,
        "csrf": ensure_admin_csrf(request),
    }
    return templates.TemplateResponse("admin/overview.html", context)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: AsyncSession = Depends(db_session)):
    require_admin(request)
    settings_service = InstanceSettingsService(session)
    settings = await settings_service.get_settings()
    context = {
        "request": request,
        "settings": settings,
        "csrf": ensure_admin_csrf(request),
    }
    return templates.TemplateResponse("admin/settings.html", context)


@router.post("/settings")
async def save_settings(
    request: Request,
    session: AsyncSession = Depends(db_session),
    site_name: str = Form("Imprint"),
    site_tagline: Optional[str] = Form(None),
    site_description: Optional[str] = Form(None),
    public_base_url: Optional[str] = Form(None),
    default_relays: Optional[str] = Form(None),
    instance_nostr_address: Optional[str] = Form(None),
    instance_admin_npub: Optional[str] = Form(None),
    lightning_address: Optional[str] = Form(None),
    donation_message: Optional[str] = Form(None),
    enable_payments: Optional[str] = Form(None),
    enable_public_essays_feed: Optional[str] = Form(None),
    enable_registrationless_readonly: Optional[str] = Form(None),
    max_feed_items: Optional[str] = Form(None),
    session_default_minutes: Optional[str] = Form(None),
    theme_accent: Optional[str] = Form(None),
    admin_allowlist: Optional[str] = Form(None),
    blocked_pubkeys: Optional[str] = Form(None),
    filter_recently_published_to_imprint_only: Optional[str] = Form(None),
    csrf: str = Form(""),
):
    require_admin(request)
    validate_admin_csrf(request, csrf)
    form_data = {
        "site_name": site_name,
        "site_tagline": site_tagline,
        "site_description": site_description,
        "public_base_url": public_base_url,
        "default_relays": default_relays,
        "instance_nostr_address": instance_nostr_address,
        "instance_admin_npub": instance_admin_npub,
        "lightning_address": lightning_address,
        "donation_message": donation_message,
        "enable_payments": enable_payments,
        "enable_public_essays_feed": enable_public_essays_feed,
        "enable_registrationless_readonly": enable_registrationless_readonly,
        "max_feed_items": max_feed_items,
        "session_default_minutes": session_default_minutes,
        "theme_accent": theme_accent,
        "admin_allowlist": admin_allowlist,
        "blocked_pubkeys": blocked_pubkeys,
        "filter_recently_published_to_imprint_only": filter_recently_published_to_imprint_only,
    }
    try:
        payload = coerce_payload(form_data)
    except ValidationError as exc:
        return templates.TemplateResponse(
            "admin/settings.html",
            {
                "request": request,
                "settings": form_data,
                "errors": exc.errors(),
                "csrf": ensure_admin_csrf(request),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    settings_service = InstanceSettingsService(session)
    auth_session = get_auth_session(request)
    updated_by = auth_session.pubkey_hex if auth_session else None
    settings_obj = await settings_service.update_settings(payload, updated_by)
    request.state.instance_settings = settings_obj
    context = {
        "request": request,
        "settings": settings_obj,
        "saved": True,
        "csrf": ensure_admin_csrf(request),
    }
    return templates.TemplateResponse("admin/settings.html", context)


@router.get("/health")
async def admin_health(request: Request):
    require_admin(request)
    return {"status": "ok", "is_admin": True}


@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request, session: AsyncSession = Depends(db_session)):
    require_admin(request)
    settings_service = InstanceSettingsService(session)
    settings = await settings_service.get_settings()
    context = {
        "request": request,
        "settings": settings,
        "csrf": ensure_admin_csrf(request),
    }
    return templates.TemplateResponse("admin/backup.html", context)


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    session: AsyncSession = Depends(db_session),
    limit: int = 100,
):
    require_admin(request)
    result = await session.execute(
        select(models.AdminEvent).order_by(models.AdminEvent.created_at.desc()).limit(limit)
    )
    events = result.scalars().all()
    context = {
        "request": request,
        "events": events,
        "limit": limit,
        "csrf": ensure_admin_csrf(request),
    }
    return templates.TemplateResponse("admin/logs.html", context)


def _normalize_relay_url(value: str) -> str:
    relay_url = value.strip()
    parsed = urlparse(relay_url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
        raise ValueError("Relay must use ws:// or wss:// and include a host")
    return relay_url


@router.post("/relays/test", response_class=HTMLResponse)
async def test_relays(
    request: Request,
    session: AsyncSession = Depends(db_session),
    default_relays: Optional[str] = Form(None),
    csrf: str = Form(""),
):
    require_admin(request)
    validate_admin_csrf(request, csrf)
    if default_relays is None:
        settings = await InstanceSettingsService(session).get_settings()
        default_relays = settings.default_relays or ""
    try:
        payload = InstanceSettingsPayload.model_validate({"default_relays": default_relays})
        relay_list = payload.relays_list()
    except ValidationError as exc:
        return templates.TemplateResponse(
            "admin/partials/relay_test_results.html",
            {"request": request, "errors": exc.errors(), "relay_results": [], "tested": False},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    async def _check(relay: str):
        try:
            async with websockets.connect(relay, open_timeout=4, close_timeout=4) as ws:
                await ws.close()
            return {"relay": relay, "status": "ok", "detail": "Handshake ok"}
        except Exception as exc:  # noqa: BLE001
            return {"relay": relay, "status": "failed", "detail": f"{type(exc).__name__}"}

    results = []
    if relay_list:
        results = await asyncio.gather(*(_check(relay) for relay in relay_list))
    auth_session = get_auth_session(request)
    await AdminEventService(session).log_event(
        action="relays_tested",
        level="info",
        message=f"Tested {len(relay_list)} relays",
        actor_pubkey=auth_session.pubkey_hex if auth_session else None,
        metadata={"results": results},
    )
    return templates.TemplateResponse(
        "admin/partials/relay_test_results.html",
        {"request": request, "errors": [], "relay_results": results, "tested": True},
    )


@router.post("/relays/add")
async def add_default_relay(
    request: Request,
    session: AsyncSession = Depends(db_session),
    relay_url: str = Form(""),
    csrf: str = Form(""),
):
    require_admin(request)
    validate_admin_csrf(request, csrf)
    settings_service = InstanceSettingsService(session)
    settings_obj = await settings_service.get_settings()
    try:
        relay_url = _normalize_relay_url(relay_url)
    except ValueError as exc:
        context = {
            "request": request,
            "settings": settings_obj,
            "csrf": ensure_admin_csrf(request),
            "relay_error": str(exc),
        }
        return templates.TemplateResponse("admin/settings.html", context, status_code=status.HTTP_400_BAD_REQUEST)

    relays = [relay.strip() for relay in (settings_obj.default_relays or "").split(",") if relay.strip()]
    if relay_url not in relays:
        relays.append(relay_url)
        settings_obj.default_relays = ",".join(relays)
        auth_session = get_auth_session(request)
        settings_obj.updated_by_pubkey = auth_session.pubkey_hex if auth_session else None
        await session.commit()
        await AdminEventService(session).log_event(
            action="default_relay_added",
            level="info",
            message=f"Added default relay {relay_url}",
            actor_pubkey=settings_obj.updated_by_pubkey,
        )
    return RedirectResponse(url="/admin/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/relays/remove")
async def remove_default_relay(
    request: Request,
    session: AsyncSession = Depends(db_session),
    relay_url: str = Form(""),
    csrf: str = Form(""),
):
    require_admin(request)
    validate_admin_csrf(request, csrf)
    settings_service = InstanceSettingsService(session)
    settings_obj = await settings_service.get_settings()
    relays = [relay.strip() for relay in (settings_obj.default_relays or "").split(",") if relay.strip()]
    if relay_url in relays:
        relays = [relay for relay in relays if relay != relay_url]
        settings_obj.default_relays = ",".join(relays) if relays else None
        auth_session = get_auth_session(request)
        settings_obj.updated_by_pubkey = auth_session.pubkey_hex if auth_session else None
        await session.commit()
        await AdminEventService(session).log_event(
            action="default_relay_removed",
            level="info",
            message=f"Removed default relay {relay_url}",
            actor_pubkey=settings_obj.updated_by_pubkey,
        )
    return RedirectResponse(url="/admin/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/backup/create")
async def backup_create(request: Request, session: AsyncSession = Depends(db_session), csrf: str = Form("")):
    require_admin(request)
    validate_admin_csrf(request, csrf)
    buffer, name = await create_backup_archive(session)
    auth_session = get_auth_session(request)
    await AdminEventService(session).log_event(
        action="backup_created",
        level="info",
        message=f"Backup created: {name}",
        actor_pubkey=auth_session.pubkey_hex if auth_session else None,
    )
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


@router.post("/restore/apply")
async def restore_apply(
    request: Request,
    session: AsyncSession = Depends(db_session),
    archive: UploadFile = File(...),
    confirm: str = Form(""),
    csrf: str = Form(""),
):
    require_admin(request)
    validate_admin_csrf(request, csrf)
    if confirm.strip().upper() != "RESTORE":
        return templates.TemplateResponse(
            "admin/backup.html",
            {
                "request": request,
                "settings": await InstanceSettingsService(session).get_settings(),
                "csrf": ensure_admin_csrf(request),
                "error": "Type RESTORE to confirm.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    content = await archive.read()
    valid, error = validate_backup_archive(content)
    if not valid:
        return templates.TemplateResponse(
            "admin/backup.html",
            {
                "request": request,
                "settings": await InstanceSettingsService(session).get_settings(),
                "csrf": ensure_admin_csrf(request),
                "error": error or "Invalid archive",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    await apply_restore_from_archive(content)
    auth_session = get_auth_session(request)
    await AdminEventService(session).log_event(
        action="restore_applied",
        level="warn",
        message=f"Restore applied from {archive.filename}",
        actor_pubkey=auth_session.pubkey_hex if auth_session else None,
    )
    return templates.TemplateResponse(
        "admin/backup.html",
        {
            "request": request,
            "settings": await InstanceSettingsService(session).get_settings(),
            "csrf": ensure_admin_csrf(request),
            "restored": True,
        },
    )

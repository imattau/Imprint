import json
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.schemas import SessionMode
from app.auth.service import (
    clear_session,
    create_local_session,
    create_nip07_session,
    create_nip46_session,
    create_readonly_session,
    get_auth_session,
    is_htmx,
    local_signer_available,
    parse_bunker_uri,
    validate_signed_event_payload,
)
from app.template_utils import register_filters
from app.nostr.event import verify_event
from app.config import settings


router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")
register_filters(templates)


@router.get("/modal", response_class=HTMLResponse)
async def auth_modal(request: Request):
    session = get_auth_session(request)
    context = {
        "request": request,
        "session": session,
        "settings": settings,
        "local_signer_available": local_signer_available(),
    }
    return templates.TemplateResponse("partials/auth_modal.html", context)


@router.get("/status", response_class=HTMLResponse)
async def auth_status(request: Request):
    session = getattr(request.state, "session", None) or get_auth_session(request)
    return templates.TemplateResponse("partials/auth_status.html", {"request": request, "session": session})


def _safe_redirect_target(request: Request) -> str:
    referer = request.headers.get("referer") or ""
    parsed = urlparse(referer)
    if parsed.netloc and parsed.hostname != request.url.hostname:
        return "/"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


async def _login_response(request: Request):
    if is_htmx(request):
        response = await auth_status(request)
        response.headers["HX-Trigger"] = "authChanged"
        return response
    return RedirectResponse(url=_safe_redirect_target(request), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/login/readonly", response_class=HTMLResponse)
async def login_readonly(request: Request, npub: str = Form(...), duration: str = Form("1h")):
    instance_settings = getattr(request.state, "instance_settings", None)
    if instance_settings and not instance_settings.enable_registrationless_readonly:
        raise HTTPException(status_code=403, detail="Read-only sessions disabled")
    default_minutes = instance_settings.session_default_minutes if instance_settings else 60
    create_readonly_session(request, npub, duration, default_minutes=default_minutes)
    return await _login_response(request)


@router.post("/login/nip07", response_class=HTMLResponse)
async def login_nip07(request: Request, payload: Any = Body(...)):
    if isinstance(payload, dict):
        pubkey_hex = payload.get("pubkey")
        duration = payload.get("duration", "1h")
    else:
        try:
            data = json.loads(payload)
            pubkey_hex = data.get("pubkey")
            duration = data.get("duration", "1h")
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=400, detail="Invalid payload") from exc
    if not pubkey_hex:
        raise HTTPException(status_code=400, detail="Missing pubkey")
    instance_settings = getattr(request.state, "instance_settings", None)
    default_minutes = instance_settings.session_default_minutes if instance_settings else 60
    create_nip07_session(request, pubkey_hex, duration, default_minutes=default_minutes)
    return await _login_response(request)


@router.post("/login/nip46", response_class=HTMLResponse)
async def login_nip46(
    request: Request,
    bunker: str = Form(""),
    signer_pubkey: str = Form(""),
    relay: str = Form(""),
    duration: str = Form("1h"),
):
    parsed = {"signer_pubkey": signer_pubkey, "relay": relay}
    if bunker:
        parsed = parse_bunker_uri(bunker)
    if not parsed.get("signer_pubkey"):
        raise HTTPException(status_code=400, detail="Signer pubkey required")
    relay_url = parsed.get("relay") or relay
    instance_settings = getattr(request.state, "instance_settings", None)
    default_minutes = instance_settings.session_default_minutes if instance_settings else 60
    create_nip46_session(request, parsed["signer_pubkey"], relay_url, duration, default_minutes=default_minutes)
    return await _login_response(request)


@router.post("/login/local", response_class=HTMLResponse)
async def login_local(request: Request, duration: str = Form("1h")):
    client_host = request.client.host if request.client else ""
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver", "testclient"}
    if client_host not in allowed_hosts:
        raise HTTPException(status_code=403, detail="Local signer available only from localhost")
    if not local_signer_available():
        raise HTTPException(status_code=400, detail="Local signer unavailable")
    instance_settings = getattr(request.state, "instance_settings", None)
    default_minutes = instance_settings.session_default_minutes if instance_settings else 60
    create_local_session(request, duration, default_minutes=default_minutes)
    return await _login_response(request)


@router.post("/logout", response_class=HTMLResponse)
async def logout(request: Request):
    clear_session(request)
    if is_htmx(request):
        return await auth_status(request)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/nip07/sign", response_class=JSONResponse)
async def nip07_submit_signed_event(request: Request, event: Any = Body(...)):
    session = get_auth_session(request)
    if not session or session.session_mode != SessionMode.nip07:
        raise HTTPException(status_code=403, detail="NIP-07 session required")
    validate_signed_event_payload(event, session.pubkey_hex or "")
    if not verify_event(event):
        raise HTTPException(status_code=400, detail="Invalid signature")
    return {"status": "ok"}

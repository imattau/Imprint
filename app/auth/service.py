import datetime as dt
import json
import logging
import secrets
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.schemas import SessionData, SessionMode
from app.config import settings
from app.nostr.key import NostrKeyError, decode_nip19, derive_pubkey_hex, encode_npub, load_private_key

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")


def parse_duration(duration: str | None, default_minutes: int = 60) -> Optional[dt.datetime]:
    now = dt.datetime.now(dt.timezone.utc)
    options = {
        "15m": dt.timedelta(minutes=15),
        "1h": dt.timedelta(hours=1),
        "24h": dt.timedelta(hours=24),
    }
    if duration == "session":
        return None
    if duration in options:
        return now + options[duration]
    if duration and duration.endswith("m") and duration[:-1].isdigit():
        minutes = int(duration[:-1])
        return now + dt.timedelta(minutes=minutes)
    return now + dt.timedelta(minutes=default_minutes)


def set_session(request: Request, data: SessionData) -> None:
    if settings.debug:
        logger.debug("Setting session keys %s", list(data.model_dump().keys()))
    request.session["session"] = data.model_dump(mode="json")


def clear_session(request: Request) -> None:
    request.session.pop("session", None)


def get_auth_session(request: Request) -> Optional[SessionData]:
    raw = request.session.get("session") if hasattr(request, "session") else None
    if not raw:
        return None
    try:
        session = SessionData(**raw)
        if session.is_expired():
            if settings.debug:
                logger.debug("Session expired for request %s", request.url.path)
            clear_session(request)
            return None
        return session
    except Exception:
        clear_session(request)
        return None


def require_signing_session(request: Request) -> SessionData:
    session = get_auth_session(request)
    if not session:
        raise AuthRequired(auth_required_response(request, status.HTTP_401_UNAUTHORIZED, require_signing=True))
    if session.session_mode == SessionMode.readonly:
        # Signing actions cannot proceed with a read-only session; return a hard 403 for form submissions.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signer session required")
    return session


class AuthRequired(Exception):
    def __init__(self, response: HTMLResponse | RedirectResponse | JSONResponse):
        self.response = response


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


def prefers_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return False
    return "application/json" in accept


def auth_required_response(
    request: Request, status_code: int = status.HTTP_401_UNAUTHORIZED, require_signing: bool = False
) -> HTMLResponse | RedirectResponse | JSONResponse:
    reason = "Sign in required" if not require_signing else "Signer session required"
    if prefers_json(request):
        return JSONResponse({"detail": reason}, status_code=status_code)

    if is_htmx(request):
        headers = {"HX-Trigger": json.dumps({"openAuthModal": True})}
        context = {"request": request, "reason": reason, "require_signing": require_signing}
        return templates.TemplateResponse(
            "fragments/auth_required.html",
            context,
            status_code=status_code,
            headers=headers,
        )

    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    redirect_target = f"/?signin=1&next={quote_plus(next_path)}"
    return RedirectResponse(url=redirect_target, status_code=status.HTTP_303_SEE_OTHER)


def require_user(request: Request, allow_readonly: bool = False, require_signing: bool = False) -> SessionData:
    session = get_auth_session(request)
    if not session:
        raise AuthRequired(auth_required_response(request, status_code=status.HTTP_401_UNAUTHORIZED))
    if session.session_mode == SessionMode.readonly and (require_signing or not allow_readonly):
        raise AuthRequired(auth_required_response(request, status_code=status.HTTP_403_FORBIDDEN, require_signing=True))
    return session


def create_session_from_pubkey(pubkey_hex: str, mode: SessionMode, duration: str | None, default_minutes: int = 60) -> SessionData:
    npub = encode_npub(pubkey_hex)
    expires_at = parse_duration(duration, default_minutes)
    return SessionData(session_mode=mode, pubkey_hex=pubkey_hex, npub=npub, expires_at=expires_at)


def parse_bunker_uri(uri: str) -> dict[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme not in {"bunker", "nostr+tcp", "nostr+ws"}:
        raise HTTPException(status_code=400, detail="Invalid bunker URI")
    signer_pubkey = parsed.netloc or parsed.path.lstrip("/")
    query = parse_qs(parsed.query)
    relay = query.get("relay", [settings.nip46_default_relay])[0]
    return {"signer_pubkey": signer_pubkey, "relay": relay}


def create_readonly_session(request: Request, npub: str, duration: str | None, default_minutes: int = 60) -> SessionData:
    try:
        pubkey_hex = decode_nip19(npub.strip().lower())
    except NostrKeyError as exc:
        raise HTTPException(status_code=400, detail="Invalid npub") from exc
    session = create_session_from_pubkey(pubkey_hex, SessionMode.readonly, duration, default_minutes)
    set_session(request, session)
    return session


def create_nip07_session(request: Request, pubkey_hex: str, duration: str | None, default_minutes: int = 60) -> SessionData:
    session = create_session_from_pubkey(pubkey_hex, SessionMode.nip07, duration, default_minutes)
    set_session(request, session)
    return session


def create_nip46_session(request: Request, signer_pubkey: str, relay: str, duration: str | None, default_minutes: int = 60) -> SessionData:
    try:
        signer_hex = decode_nip19(signer_pubkey) if signer_pubkey.startswith("npub") else signer_pubkey
    except NostrKeyError as exc:
        raise HTTPException(status_code=400, detail="Invalid signer key") from exc
    client_secret = secrets.token_hex(32)
    expires_at = parse_duration(duration, default_minutes)
    npub = encode_npub(signer_hex)
    session = SessionData(
        session_mode=SessionMode.nip46,
        pubkey_hex=signer_hex,
        npub=npub,
        signer_pubkey=signer_hex,
        relay=relay,
        client_secret=client_secret,
        expires_at=expires_at,
    )
    set_session(request, session)
    return session


def create_local_session(request: Request, duration: str | None, default_minutes: int = 60) -> SessionData:
    try:
        sk = load_private_key(settings.nostr_secret)
        pubkey_hex = derive_pubkey_hex(sk)
    except NostrKeyError as exc:
        raise HTTPException(status_code=400, detail="Local signer unavailable") from exc
    session = create_session_from_pubkey(pubkey_hex, SessionMode.local, duration, default_minutes)
    set_session(request, session)
    return session


def local_signer_available() -> bool:
    """Return True if a usable local signer key is configured."""

    if not settings.nostr_secret:
        return False
    try:
        load_private_key(settings.nostr_secret)
        return True
    except NostrKeyError:
        return False


def validate_signed_event_payload(event: Dict[str, Any], expected_pubkey: str) -> None:
    if not event:
        raise HTTPException(status_code=400, detail="Missing event")
    pubkey = event.get("pubkey")
    if pubkey != expected_pubkey:
        raise HTTPException(status_code=400, detail="Mismatched signer")

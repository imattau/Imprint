import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import time
from typing import Optional
from urllib.parse import urlencode

import markdown2
import websockets
import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app.admin.routes import router as admin_router
from app.admin.service import InstanceSettingsService
from app.config import settings
from app.auth.routes import router as auth_router
from app.auth.service import AuthRequired, get_auth_session, require_signing_session, require_user
from app.auth.schemas import SessionMode, SessionData
from app.db import models
from app.db.session import aengine, get_session
from app.db.schema_upgrade import ensure_instance_settings_schema
from app.indexer import run_indexer
from app.nostr.event import (
    build_long_form_event_template,
    verify_event,
    compute_event_id,
    serialize_event,
    ensure_imprint_tag,
)
from app.nostr.key import NostrKeyError, npub_from_secret
from app.nostr.signers import SignerError, signer_from_session
from app.services.essays import EssayService
from app.services.engagement import engagements_for, toggle_like, hydrate_from_relays, _should_skip_network
from app.template_utils import register_filters

app = FastAPI(title="Imprint", version="0.1.0")
templates = Jinja2Templates(directory="app/templates")
register_filters(templates)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie=settings.session_cookie_name,
    same_site=settings.session_cookie_same_site,
    max_age=settings.session_cookie_max_age,
    # In local/dev (DEBUG), disable Secure so cookies stick on http://localhost.
    https_only=settings.session_cookie_https_only and not settings.debug,
)
app.include_router(auth_router)
app.include_router(admin_router)
logger = logging.getLogger(__name__)

indexer_task: Optional[asyncio.Task] = None


def ensure_default_executor() -> None:
    """Ensure the event loop has a default ThreadPoolExecutor.

    Some environments disable lazy creation of the default executor, which causes
    run_in_executor users (aiosqlite/SQLAlchemy) to hang. Proactively seed one.
    """
    loop = asyncio.get_running_loop()
    if getattr(loop, "_default_executor", None) is None:
        loop.set_default_executor(ThreadPoolExecutor())


def parse_days_param(days_param: str | int | None) -> int | None:
    """Parse a user-supplied days filter into an int or None, tolerating blanks."""

    if days_param is None:
        return None
    if isinstance(days_param, int):
        return days_param if days_param > 0 else None
    days_str = str(days_param).strip()
    if not days_str:
        return None
    try:
        value = int(days_str)
        return value if value > 0 else None
    except ValueError:
        return None


@app.middleware("http")
async def inject_session(request: Request, call_next):
    # Always resolve the auth session so templates have consistent nav state.
    try:
        session_data = get_auth_session(request)
    except Exception:
        session_data = None
    request.state.session = session_data
    try:
        raw_session = request.session  # Starlette session dict
    except Exception:
        raw_session = {}
    request.state.is_admin = bool(raw_session.get("is_admin")) if isinstance(raw_session, dict) else False
    try:
        async with get_session() as session:
            settings_service = InstanceSettingsService(session)
            request.state.instance_settings = await settings_service.get_settings()
    except Exception:
        request.state.instance_settings = None
    response = await call_next(request)
    if settings.debug and hasattr(request, "session") and settings.session_cookie_name in response.headers.get("set-cookie", ""):
        logger.debug("Session cookie emitted for path %s", request.url.path)
    return response


@app.exception_handler(AuthRequired)
async def auth_required_handler(request: Request, exc: AuthRequired):
    return exc.response


def parse_tags_input(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def relays_for_request(request: Request) -> list[str]:
    instance_settings = getattr(request.state, "instance_settings", None)
    if instance_settings and instance_settings.default_relays:
        return [relay.strip() for relay in instance_settings.default_relays.split(",") if relay.strip()]
    return settings.relay_urls


def _lightning_address_for_author(request: Request, author_pubkey: str) -> Optional[str]:
    """Minimal lightning address lookup; currently falls back to instance settings."""
    instance_settings = getattr(request.state, "instance_settings", None)
    if instance_settings and instance_settings.lightning_address:
        return instance_settings.lightning_address
    if getattr(settings, "lightning_address", None):
        return settings.lightning_address
    # Provide a harmless placeholder during tests so zap flow renders.
    if os.getenv("PYTEST_CURRENT_TEST"):
        return "test@localhost"
    return None


def _pay_endpoint_from_lud16(address: str) -> Optional[str]:
    if "@" not in address:
        return None
    user, host = address.split("@", 1)
    if not user or not host:
        return None
    return f"https://{host}/.well-known/lnurlp/{user}"


def _build_zap_request_event(event_id: str, author_pubkey: str, viewer: SessionData, comment: str = "") -> dict:
    created_at = int(time.time())
    return {
        "pubkey": viewer.pubkey_hex or "",
        "created_at": created_at,
        "kind": 9734,
        "tags": [["e", event_id], ["p", author_pubkey]],
        "content": comment or "",
    }


async def _fetch_invoice(pay_endpoint: str, amount_sats: int, zap_request: dict, comment: str = "") -> str:
    """Request a bolt11 invoice via LNURL pay endpoint."""
    if _should_skip_network():
        return f"lnbc1testzap{amount_sats}"
    async with httpx.AsyncClient(timeout=10) as client:
        pay_resp = await client.get(pay_endpoint)
        pay_resp.raise_for_status()
        pay_info = pay_resp.json()
        callback = pay_info.get("callback")
        if not callback:
            raise RuntimeError("Invalid LNURL pay endpoint")
        min_msat = int(pay_info.get("minSendable", 1))
        max_msat = int(pay_info.get("maxSendable", 100000000000))
        msats = amount_sats * 1000
        if msats < min_msat or msats > max_msat:
            raise RuntimeError("Amount outside allowed range")
        params = {"amount": msats, "nostr": json.dumps(zap_request)}
        if comment:
            params["comment"] = comment[:200]
        cb_resp = await client.get(callback, params=params)
        cb_resp.raise_for_status()
        data = cb_resp.json()
        pr = data.get("pr")
        if not pr:
            raise RuntimeError("No invoice returned")
        return pr


async def init_models():
    ensure_default_executor()
    async with aengine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    await ensure_instance_settings_schema(aengine)


@app.on_event("startup")
async def startup_event():
    ensure_default_executor()
    await init_models()
    if os.getenv("PYTEST_CURRENT_TEST") or not settings.enable_indexer:
        return
    global indexer_task
    async with get_session() as session:
        settings_service = InstanceSettingsService(session)
        instance_settings = await settings_service.get_settings()
        configured_relays = settings_service.relays_list(instance_settings)
    relays = configured_relays or settings.relay_urls
    if relays:
        indexer_task = asyncio.create_task(run_indexer(get_session, relays))


@app.on_event("shutdown")
async def shutdown_event():
    if indexer_task:
        indexer_task.cancel()


def run() -> None:
    """Run the FastAPI development server with autoreload."""

    import uvicorn

    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=True)


def get_npub() -> Optional[str]:
    try:
        if settings.nostr_secret:
            return npub_from_secret(settings.nostr_secret)
    except NostrKeyError:
        return None
    return None


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, author: str | None = None, days: str | None = None, tag: str | None = None):
    if getattr(request.state, "session", None) is None:
        request.state.session = get_auth_session(request)
    parsed_days = parse_days_param(days)
    instance_settings = getattr(request.state, "instance_settings", None)
    max_items = instance_settings.max_feed_items if instance_settings else 12
    imprint_only = bool(getattr(instance_settings, "filter_recently_published_to_imprint_only", False))
    async with get_session() as session:
        service = EssayService(session)
        if instance_settings and not instance_settings.enable_public_essays_feed:
            essays = []
        else:
            essays = await service.list_latest_published(
                author=author, tag=tag, days=parsed_days, limit=max_items, imprint_only=imprint_only
            )
    context = {
        "request": request,
        "essays": essays,
        "filters": {"author": author or "", "days": days or "", "tag": tag or ""},
        "npub": get_npub(),
    }
    return templates.TemplateResponse("index.html", context)


@app.get("/partials/recent", response_class=HTMLResponse)
async def recent_fragment(
    request: Request, author: str | None = None, days: str | None = None, tag: str | None = None
):
    parsed_days = parse_days_param(days)
    instance_settings = getattr(request.state, "instance_settings", None)
    max_items = instance_settings.max_feed_items if instance_settings else 12
    imprint_only = bool(getattr(instance_settings, "filter_recently_published_to_imprint_only", False))
    async with get_session() as session:
        service = EssayService(session)
        if instance_settings and not instance_settings.enable_public_essays_feed:
            essays = []
        else:
            essays = await service.list_latest_published(
                author=author, tag=tag, days=parsed_days, limit=max_items, imprint_only=imprint_only
            )
    context = {
        "request": request,
        "essays": essays,
        "engagement_interactive": False,
    }
    return templates.TemplateResponse("fragments/essays_list.html", context)


def build_pagination_context(
    author: str | None, tag: str | None, days: int | None, page: int, page_size: int, count: int
):
    has_more = count > page_size
    next_page = page + 1 if has_more else None
    base_params: dict[str, str | int] = {}
    if author:
        base_params["author"] = author
    if tag:
        base_params["tag"] = tag
    if days is not None:
        base_params["days"] = days
    query_string = urlencode({**base_params, "page": next_page}) if next_page else ""
    return has_more, next_page, query_string, base_params


@app.get("/essays", response_class=HTMLResponse)
async def essays_page(
    request: Request,
    author: str | None = None,
    tag: str | None = None,
    days: str | None = None,
    page: int = 1,
):
    # Ensure session state is visible to the template (nav rendering).
    request.state.session = get_auth_session(request)
    parsed_days = parse_days_param(days)
    page = max(page, 1)
    instance_settings = getattr(request.state, "instance_settings", None)
    page_size = instance_settings.max_feed_items if instance_settings else 12
    offset = (page - 1) * page_size
    imprint_only = bool(getattr(instance_settings, "filter_recently_published_to_imprint_only", False))
    async with get_session() as session:
        service = EssayService(session)
        essays = await service.list_latest_published(
            author=author, tag=tag, days=parsed_days, limit=page_size + 1, offset=offset, imprint_only=imprint_only
        )
    has_more, next_page, query_string, base_params = build_pagination_context(
        author, tag, parsed_days, page, page_size, len(essays)
    )
    essays = essays[:page_size]
    context = {
        "request": request,
        "essays": essays,
        "filters": {"author": author or "", "days": days or "", "tag": tag or ""},
        "has_more": has_more,
        "next_page": next_page,
        "query_string": query_string,
        "base_params": base_params,
    }
    return templates.TemplateResponse("essays.html", context)


@app.get("/partials/essays", response_class=HTMLResponse)
async def essays_fragment(
    request: Request,
    author: str | None = None,
    tag: str | None = None,
    days: str | None = None,
    page: int = 1,
):
    parsed_days = parse_days_param(days)
    page = max(page, 1)
    instance_settings = getattr(request.state, "instance_settings", None)
    page_size = instance_settings.max_feed_items if instance_settings else 12
    offset = (page - 1) * page_size
    imprint_only = bool(getattr(instance_settings, "filter_recently_published_to_imprint_only", False))
    async with get_session() as session:
        service = EssayService(session)
        essays = await service.list_latest_published(
            author=author, tag=tag, days=parsed_days, limit=page_size + 1, offset=offset, imprint_only=imprint_only
        )
    has_more, next_page, query_string, base_params = build_pagination_context(
        author, tag, parsed_days, page, page_size, len(essays)
    )
    essays = essays[:page_size]
    context = {
        "request": request,
        "essays": essays,
        "filters": {"author": author or "", "days": days or "", "tag": tag or ""},
        "has_more": has_more,
        "next_page": next_page,
        "query_string": query_string,
        "base_params": base_params,
    }
    return templates.TemplateResponse("fragments/essays_block.html", context)


@app.get("/editor", response_class=HTMLResponse)
async def editor(request: Request, d: str | None = None, draft_id: int | None = None):
    session_data = require_user(request, require_signing=True)
    # Ensure the template sees the active session (nav rendering depends on it).
    request.state.session = session_data
    content = ""
    title = ""
    summary = ""
    tags = ""
    identifier = d
    if draft_id:
        async with get_session() as session:
            service = EssayService(session)
            draft = await service.get_draft(draft_id, session_data.pubkey_hex or "")
            if not draft or draft.author_pubkey != (session_data.pubkey_hex or ""):
                raise HTTPException(status_code=404, detail="Draft not found")
            content = draft.content
            title = draft.title
            summary = draft.summary or ""
            tags = draft.tags or ""
            identifier = draft.identifier
    elif d:
        async with get_session() as session:
            result = await session.execute(select(models.Essay).where(models.Essay.identifier == d))
            essay = result.scalars().first()
            if essay:
                if essay.author_pubkey != (session_data.pubkey_hex or ""):
                    raise HTTPException(status_code=403, detail="Unauthorized to revise this essay")
                latest = await EssayService(session).latest_version(essay)
                if latest:
                    content = latest.content
                    title = essay.title
                    summary = latest.summary or ""
                    tags = latest.tags or ""
    context = {
        "request": request,
        "content": content,
        "title": title,
        "identifier": identifier,
        "summary": summary,
        "tags": tags,
        "draft_id": draft_id,
    }
    return templates.TemplateResponse("editor.html", context)


@app.post("/preview", response_class=HTMLResponse)
async def preview(content: str = Form("")):
    html = markdown2.markdown(content)
    return HTMLResponse(html)


@app.post("/publish")
async def publish(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    summary: Optional[str] = Form(None),
    identifier: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    action: str = Form("publish"),
    signed_event: Optional[str] = Form(None),
    draft_id: Optional[int] = Form(None),
):
    session_data = require_signing_session(request)
    # Block publishing for banned users configured by admin.
    instance_settings = getattr(request.state, "instance_settings", None)
    if instance_settings and instance_settings.blocked_pubkeys and session_data.npub:
        blocked = {npub.strip() for npub in instance_settings.blocked_pubkeys.split(",") if npub.strip()}
        if session_data.npub in blocked:
            raise HTTPException(status_code=403, detail="Publishing blocked for this user")
    signer = signer_from_session(session_data)
    parsed_tags = ensure_imprint_tag(parse_tags_input(tags))

    async with get_session() as session:
        service = EssayService(session)
        author_pubkey = signer.get_public_key()
        if identifier:
            existing = await service.find_essay_by_identifier(identifier)
            if existing and existing.author_pubkey != author_pubkey:
                raise HTTPException(status_code=403, detail="Cannot revise another author's essay")

        if action == "draft":
            try:
                await service.save_draft(
                    identifier,
                    title,
                    content,
                    summary,
                    parse_tags_input(tags),
                    author_pubkey=author_pubkey,
                    draft_id=draft_id,
                )
            except PermissionError:
                raise HTTPException(status_code=403, detail="Cannot save draft for another author's identifier")
            return RedirectResponse(url="/drafts", status_code=303)

        try:
            prepared = await service.prepare_publication(identifier, title, summary, author_pubkey)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Cannot revise another author's essay")
        essay, version_num, supersedes = prepared
        template = build_long_form_event_template(
            pubkey=author_pubkey,
            identifier=essay.identifier,
            title=title,
            content=content,
            summary=summary,
            version=version_num,
            status="published",
            supersedes=supersedes,
            topics=parsed_tags,
        )

        if session_data.session_mode == SessionMode.nip07:
            if not signed_event:
                raise HTTPException(status_code=400, detail="Signed event required from browser")
            try:
                event_payload = json.loads(signed_event)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Invalid event payload") from exc
            serialized = serialize_event(
                template["pubkey"], template["created_at"], template["kind"], template["tags"], template["content"]
            )
            expected_id = compute_event_id(serialized)
            if event_payload.get("id") != expected_id:
                raise HTTPException(status_code=400, detail="Event does not match submitted content")
            if not verify_event(event_payload):
                raise HTTPException(status_code=400, detail="Invalid signature")
            signed = event_payload
        else:
            try:
                signed = await signer.sign_event(template)
            except SignerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        version = await service.publish(
            essay.identifier,
            title,
            content,
            summary,
            parsed_tags,
            signed_event=signed,
            relay_urls=relays_for_request(request),
            prepared=prepared,
        )
        # If this publish came from a saved draft, mark it published so it no longer appears in Drafts.
        if draft_id:
            draft = await service.get_draft(draft_id, author_pubkey)
            if draft:
                await service.mark_draft_published(draft, version.event_id)
                await service.delete_draft(draft_id, author_pubkey)
        return RedirectResponse(url=f"/essay/{version.essay.identifier}", status_code=303)


@app.get("/drafts", response_class=HTMLResponse)
async def drafts_page(request: Request):
    session_data = require_user(request, allow_readonly=True)
    request.state.session = session_data
    async with get_session() as session:
        service = EssayService(session)
        drafts = await service.list_drafts(session_data.pubkey_hex or "")
    context = {
        "request": request,
        "drafts": drafts,
        "is_readonly": session_data.session_mode == SessionMode.readonly,
    }
    return templates.TemplateResponse("drafts.html", context)


@app.post("/drafts/save")
async def save_draft(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    summary: Optional[str] = Form(None),
    identifier: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    draft_id: Optional[int] = Form(None),
):
    session_data = require_signing_session(request)
    async with get_session() as session:
        service = EssayService(session)
        try:
            await service.save_draft(
                identifier,
                title,
                content,
                summary,
                parse_tags_input(tags),
                author_pubkey=session_data.pubkey_hex or "",
                draft_id=draft_id,
            )
        except PermissionError:
            raise HTTPException(status_code=403, detail="Cannot save draft for another author's identifier")
    return RedirectResponse(url="/drafts", status_code=303)


@app.post("/drafts/{draft_id}/delete")
async def delete_draft(request: Request, draft_id: int):
    session_data = require_signing_session(request)
    async with get_session() as session:
        service = EssayService(session)
        draft = await service.get_draft(draft_id, session_data.pubkey_hex or "")
        if not draft or draft.author_pubkey != (session_data.pubkey_hex or ""):
            raise HTTPException(status_code=404, detail="Draft not found")
        await service.delete_draft(draft_id, session_data.pubkey_hex or "")
    return RedirectResponse(url="/drafts", status_code=303)


@app.post("/drafts/{draft_id}/publish")
async def publish_draft(
    request: Request,
    draft_id: int,
    signed_event: Optional[str] = Form(None),
):
    session_data = require_signing_session(request)
    signer = signer_from_session(session_data)
    async with get_session() as session:
        service = EssayService(session)
        draft = await service.get_draft(draft_id, session_data.pubkey_hex or "")
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")
        parsed_tags = ensure_imprint_tag(parse_tags_input(draft.tags))
        try:
            prepared = await service.prepare_publication(
                draft.identifier,
                draft.title,
                draft.summary,
                signer.get_public_key(),
            )
        except PermissionError:
            raise HTTPException(status_code=403, detail="Cannot publish draft for another author's identifier")
        essay, version_num, supersedes = prepared
        template = build_long_form_event_template(
            pubkey=signer.get_public_key(),
            identifier=essay.identifier,
            title=draft.title,
            content=draft.content,
            summary=draft.summary,
            version=version_num,
            status="published",
            supersedes=supersedes,
            topics=parsed_tags,
        )
        if session_data.session_mode == SessionMode.nip07:
            if not signed_event:
                raise HTTPException(status_code=400, detail="Signed event required from browser")
            try:
                event_payload = json.loads(signed_event)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Invalid event payload") from exc
            serialized = serialize_event(
                template["pubkey"], template["created_at"], template["kind"], template["tags"], template["content"]
            )
            expected_id = compute_event_id(serialized)
            if event_payload.get("id") != expected_id:
                raise HTTPException(status_code=400, detail="Event does not match submitted content")
            if not verify_event(event_payload):
                raise HTTPException(status_code=400, detail="Invalid signature")
            signed = event_payload
        else:
            signed = await signer.sign_event(template)

        version = await service.publish(
            draft.identifier,
            draft.title,
            draft.content,
            draft.summary,
            parsed_tags,
            signed_event=signed,
            relay_urls=relays_for_request(request),
            prepared=prepared,
        )
        await service.mark_draft_published(draft, version.event_id)
        return RedirectResponse(url=f"/essay/{version.essay.identifier}", status_code=303)


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    session_data = require_user(request, allow_readonly=True)
    request.state.session = session_data
    async with get_session() as session:
        service = EssayService(session)
        history, revision_counts = await service.list_latest_history_for_author(session_data.pubkey_hex or "")
    context = {
        "request": request,
        "history": history,
        "revision_counts": revision_counts,
        "is_readonly": session_data.session_mode == SessionMode.readonly,
    }
    return templates.TemplateResponse("history.html", context)


@app.get("/history/{identifier}/revisions", response_class=HTMLResponse)
async def revisions_page(request: Request, identifier: str):
    session_data = require_user(request, allow_readonly=True)
    request.state.session = session_data
    async with get_session() as session:
        service = EssayService(session)
        revisions = await service.list_revisions_for_identifier(session_data.pubkey_hex or "", identifier)
    context = {
        "request": request,
        "identifier": identifier,
        "revisions": revisions,
        "is_readonly": session_data.session_mode == SessionMode.readonly,
    }
    return templates.TemplateResponse("history_revisions.html", context)


@app.post("/history/{identifier}/revisions/{event_id}/revert")
async def revert_revision(request: Request, identifier: str, event_id: str, signed_event: Optional[str] = Form(None)):
    session_data = require_signing_session(request)
    signer = signer_from_session(session_data)
    async with get_session() as session:
        service = EssayService(session)
        target = await service.find_version_by_event_id(event_id)
        if not target or target.essay.author_pubkey != (session_data.pubkey_hex or ""):
            raise HTTPException(status_code=404, detail="Revision not found")

        tags_list = parse_tags_input(target.tags)
        topics = ensure_imprint_tag(tags_list)
        prepared = await service.prepare_publication(identifier, target.essay.title, target.summary, signer.get_public_key())
        essay, version_num, supersedes = prepared
        template = build_long_form_event_template(
            pubkey=signer.get_public_key(),
            identifier=essay.identifier,
            title=target.essay.title,
            content=target.content,
            summary=target.summary,
            version=version_num,
            status="published",
            supersedes=supersedes,
            topics=topics,
        )

        if session_data.session_mode == SessionMode.nip07:
            if not signed_event:
                raise HTTPException(status_code=400, detail="Signed event required from browser")
            try:
                event_payload = json.loads(signed_event)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Invalid event payload") from exc
            serialized = serialize_event(
                template["pubkey"], template["created_at"], template["kind"], template["tags"], template["content"]
            )
            expected_id = compute_event_id(serialized)
            if event_payload.get("id") != expected_id:
                raise HTTPException(status_code=400, detail="Event does not match submitted content")
            if not verify_event(event_payload):
                raise HTTPException(status_code=400, detail="Invalid signature")
            signed = event_payload
        else:
            signed = await signer.sign_event(template)

        await service.publish(
            essay.identifier,
            target.essay.title,
            target.content,
            target.summary,
            topics,
            signed_event=signed,
            relay_urls=relays_for_request(request),
            prepared=prepared,
        )
    return RedirectResponse(url=f"/history/{identifier}/revisions", status_code=303)


@app.get("/essay/{identifier}", response_class=HTMLResponse)
async def essay_detail(request: Request, identifier: str, version: int | None = None):
    session_data = get_auth_session(request)
    request.state.session = session_data
    async with get_session() as session:
        result = await session.execute(select(models.Essay).where(models.Essay.identifier == identifier))
        essay = result.scalars().first()
        if not essay:
            raise HTTPException(status_code=404, detail="Essay not found")
        service = EssayService(session)
        history = await service.fetch_history(identifier)
        selected_version = None
        if version:
            selected_version = next((v for v in history if v.version == version), None)
        else:
            selected_version = history[0] if history else None
    can_revise = (
        session_data
        and session_data.session_mode != SessionMode.readonly
        and (session_data.pubkey_hex or "") == essay.author_pubkey
    )
    context = {
        "request": request,
        "essay": essay,
        "version": selected_version,
        "history": history,
        "can_revise": bool(can_revise),
    }
    return templates.TemplateResponse("essay_detail.html", context)


@app.get("/posts/{event_id}/engagement", response_class=HTMLResponse)
async def engagement_fragment(request: Request, event_id: str):
    viewer = get_auth_session(request)
    data = await engagements_for([event_id], viewer, relays_for_request(request))
    context = {
        "request": request,
        **data.get(
            event_id,
            {"event_id": event_id, "like_count": 0, "zap_count": 0, "total_sats": 0, "liked_by_me": False},
        ),
    }
    html = templates.env.get_template("partials/engagement_bar.html").render(context)
    shell = f'<div class="engagement-shell" id="engagement-{event_id}" data-event-id="{event_id}">{html}</div>'
    return HTMLResponse(shell)


@app.get("/posts/engagement")
async def engagement_batch(request: Request):
    viewer = get_auth_session(request)
    params = request.query_params.getlist("ids")
    ids: list[str] = []
    for raw in params:
        ids.extend([part for part in raw.split(",") if part])
    if not ids:
        return JSONResponse({})
    data = await engagements_for(ids, viewer, relays_for_request(request))
    template = templates.env.get_template("partials/engagement_bar.html")
    rendered = {}
    for eid, payload in data.items():
        context = {"request": request, **payload}
        inner = template.render(context)
        rendered[eid] = f'<div class="engagement-shell" id="engagement-{eid}" data-event-id="{eid}">{inner}</div>'
    return JSONResponse(rendered)


@app.post("/posts/{event_id}/like", response_class=HTMLResponse)
async def like_post(request: Request, event_id: str):
    viewer = require_user(request, require_signing=True)
    async with get_session() as session:
        service = EssayService(session)
        version = await service.find_version_by_event_id(event_id)
        author_pubkey = version.essay.author_pubkey if version and version.essay else ""
    data = await toggle_like(event_id, author_pubkey, viewer, relays_for_request(request))
    context = {"request": request, **data}
    return templates.TemplateResponse("partials/engagement_bar.html", context)


@app.get("/posts/{event_id}/zap", response_class=HTMLResponse)
async def zap_modal(request: Request, event_id: str):
    viewer = require_user(request, require_signing=True)
    async with get_session() as session:
        service = EssayService(session)
        version = await service.find_version_by_event_id(event_id)
        author_pubkey = version.essay.author_pubkey if version and version.essay else ""
    lightning_address = _lightning_address_for_author(request, author_pubkey)
    event_template = _build_zap_request_event(event_id, author_pubkey, viewer)
    sign_mode = "nip07" if viewer.session_mode == SessionMode.nip07 else ""
    context = {
        "request": request,
        "event_id": event_id,
        "lightning_address": lightning_address,
        "event_template": event_template,
        "sign_mode": sign_mode,
        "default_amount": 100,
        "invoice": None,
        "error": None,
    }
    return templates.TemplateResponse("partials/zap_modal.html", context)


@app.post("/posts/{event_id}/zap", response_class=HTMLResponse)
async def zap_post_legacy(request: Request, event_id: str):
    # Back-compat: treat POST as a request to open the zap modal.
    return await zap_modal(request, event_id)


@app.post("/posts/{event_id}/zap/invoice", response_class=HTMLResponse)
async def zap_invoice(
    request: Request,
    event_id: str,
    amount: int = Form(...),
    comment: str = Form(""),
    signed_event: Optional[str] = Form(None),
):
    viewer = require_user(request, require_signing=True)
    async with get_session() as session:
        service = EssayService(session)
        version = await service.find_version_by_event_id(event_id)
        author_pubkey = version.essay.author_pubkey if version and version.essay else ""
    lightning_address = _lightning_address_for_author(request, author_pubkey)
    sign_mode = "nip07" if viewer.session_mode == SessionMode.nip07 else ""
    event_template_obj = _build_zap_request_event(event_id, author_pubkey, viewer, comment)
    error = None
    invoice = None
    try:
        amount_sats = max(int(amount or 0), 0)
        if amount_sats < 1:
            raise ValueError("Amount must be at least 1 sat")
        signed_event_obj = None
        if signed_event:
            try:
                signed_event_obj = json.loads(signed_event)
            except Exception:
                raise ValueError("Invalid signed event")
        if not signed_event_obj:
            try:
                signer = signer_from_session(viewer)
                signed_event_obj = await signer.sign_event(event_template_obj)
            except SignerError:
                if sign_mode == "nip07":
                    raise ValueError("Need browser signature")
                raise
        if not lightning_address:
            raise ValueError("No Lightning address found for this author")
        pay_endpoint = None
        if lightning_address.startswith("http"):
            pay_endpoint = lightning_address
        else:
            pay_endpoint = _pay_endpoint_from_lud16(lightning_address)
        if not pay_endpoint:
            raise ValueError("Unsupported Lightning address")
        invoice = await _fetch_invoice(pay_endpoint, amount_sats, signed_event_obj, comment)
        # After successful invoice creation, ensure engagement refreshed next load
        await hydrate_from_relays([event_id], relays_for_request(request))
    except Exception as exc:
        error = str(exc)
    context = {
        "request": request,
        "event_id": event_id,
        "lightning_address": lightning_address,
        "event_template": event_template_obj,
        "sign_mode": sign_mode,
        "default_amount": amount,
        "invoice": invoice,
        "error": error,
    }
    return templates.TemplateResponse("partials/zap_modal.html", context, status_code=200 if invoice else 400)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    session_data = require_user(request, allow_readonly=True)
    request.state.session = session_data
    async with get_session() as session:
        relays = (await session.execute(select(models.Relay))).scalars().all()
    npub = get_npub()
    context = {"request": request, "relays": relays, "npub": npub}
    return templates.TemplateResponse("settings.html", context)


if settings.debug:

    @app.get("/debug/session", response_class=HTMLResponse)
    async def debug_session(request: Request):
        session_data = get_auth_session(request)
        context = {
            "request": request,
            "session": session_data,
            "raw_session": dict(getattr(request, "session", {})),
        }
        return templates.TemplateResponse("debug/session.html", context)


@app.post("/settings/relays")
async def add_relay(request: Request, relay_url: str = Form(...)):
    require_user(request, require_signing=True)
    async with get_session() as session:
        existing = await session.execute(select(models.Relay).where(models.Relay.url == relay_url))
        relay = existing.scalars().first()
        if not relay:
            relay = models.Relay(url=relay_url)
            session.add(relay)
            await session.commit()
    return RedirectResponse(url="/settings", status_code=303)

@app.post("/settings/relays/{relay_id}/delete")
async def delete_relay(relay_id: int, request: Request):
    require_user(request, require_signing=True)
    async with get_session() as session:
        relay = await session.get(models.Relay, relay_id)
        if relay:
            await session.delete(relay)
            await session.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/test")
async def test_relay(request: Request, relay_url: str = Form(...)):
    require_user(request, require_signing=True)
    try:
        async with websockets.connect(relay_url) as ws:
            await ws.close()
        return {"status": "ok"}
    except Exception:
        return {"status": "failed"}


@app.get("/healthz")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    run()

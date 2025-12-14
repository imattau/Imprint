import asyncio
import json
import logging
import os
from typing import Optional
from urllib.parse import urlencode

import markdown2
import websockets
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app.admin.routes import router as admin_router
from app.admin.service import InstanceSettingsService
from app.config import settings
from app.auth.routes import router as auth_router
from app.auth.service import AuthRequired, get_auth_session, require_signing_session, require_user
from app.auth.schemas import SessionMode
from app.db import models
from app.db.session import aengine, get_session
from app.indexer import run_indexer
from app.nostr.event import build_long_form_event_template, verify_event, compute_event_id, serialize_event
from app.nostr.key import NostrKeyError, encode_npub, npub_from_secret
from app.nostr.signers import SignerError, signer_from_session
from app.services.essays import EssayService

app = FastAPI(title="Imprint", version="0.1.0")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie=settings.session_cookie_name,
    same_site=settings.session_cookie_same_site,
    max_age=settings.session_cookie_max_age,
    https_only=settings.session_cookie_https_only,
)
app.include_router(auth_router)
app.include_router(admin_router)
logger = logging.getLogger(__name__)

indexer_task: Optional[asyncio.Task] = None


def markdown_filter(text: str | None):
    return Markup(markdown2.markdown(text or ""))


templates.env.filters["markdown"] = markdown_filter


def author_display(pubkey: str | None) -> str:
    if not pubkey:
        return "Unknown author"
    try:
        npub = encode_npub(pubkey)
    except Exception:
        npub = pubkey
    if len(npub) > 20:
        return f"{npub[:10]}…{npub[-4:]}"
    return npub


def tags_list(tags: str | None) -> list[str]:
    if not tags:
        return []
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


templates.env.filters["author_display"] = author_display
templates.env.filters["tags_list"] = tags_list


@app.middleware("http")
async def inject_session(request: Request, call_next):
    session_data = None
    raw_session = request.scope.get("session") if hasattr(request, "scope") else None
    try:
        if raw_session is not None:
            session_data = get_auth_session(request)
    except Exception:
        session_data = None
    request.state.session = session_data
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


async def init_models():
    async with aengine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)


@app.on_event("startup")
async def startup_event():
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
async def home(request: Request, author: str | None = None, days: int | None = None, tag: str | None = None):
    if getattr(request.state, "session", None) is None:
        request.state.session = get_auth_session(request)
    instance_settings = getattr(request.state, "instance_settings", None)
    max_items = instance_settings.max_feed_items if instance_settings else 12
    async with get_session() as session:
        service = EssayService(session)
        if instance_settings and not instance_settings.enable_public_essays_feed:
            essays = []
        else:
            essays = await service.list_latest_published(author=author, tag=tag, days=days, limit=max_items)
    context = {
        "request": request,
        "essays": essays,
        "filters": {"author": author or "", "days": days or "", "tag": tag or ""},
        "npub": get_npub(),
    }
    return templates.TemplateResponse("index.html", context)


@app.get("/partials/recent", response_class=HTMLResponse)
async def recent_fragment(request: Request, author: str | None = None, days: int | None = None, tag: str | None = None):
    instance_settings = getattr(request.state, "instance_settings", None)
    max_items = instance_settings.max_feed_items if instance_settings else 12
    async with get_session() as session:
        service = EssayService(session)
        if instance_settings and not instance_settings.enable_public_essays_feed:
            essays = []
        else:
            essays = await service.list_latest_published(author=author, tag=tag, days=days, limit=max_items)
    context = {
        "request": request,
        "essays": essays,
    }
    return templates.TemplateResponse("fragments/essays_list.html", context)


def build_pagination_context(author: str | None, tag: str | None, days: int | None, page: int, page_size: int, count: int):
    has_more = count > page_size
    next_page = page + 1 if has_more else None
    base_params = {"author": author or "", "tag": tag or "", "days": days or ""}
    query_string = urlencode({**base_params, "page": next_page}) if next_page else ""
    return has_more, next_page, query_string, base_params


@app.get("/essays", response_class=HTMLResponse)
async def essays_page(
    request: Request,
    author: str | None = None,
    tag: str | None = None,
    days: int | None = None,
    page: int = 1,
):
    page = max(page, 1)
    instance_settings = getattr(request.state, "instance_settings", None)
    page_size = instance_settings.max_feed_items if instance_settings else 12
    offset = (page - 1) * page_size
    async with get_session() as session:
        service = EssayService(session)
        essays = await service.list_latest_published(
            author=author, tag=tag, days=days, limit=page_size + 1, offset=offset
        )
    has_more, next_page, query_string, base_params = build_pagination_context(
        author, tag, days, page, page_size, len(essays)
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
    days: int | None = None,
    page: int = 1,
):
    page = max(page, 1)
    instance_settings = getattr(request.state, "instance_settings", None)
    page_size = instance_settings.max_feed_items if instance_settings else 12
    offset = (page - 1) * page_size
    async with get_session() as session:
        service = EssayService(session)
        essays = await service.list_latest_published(
            author=author, tag=tag, days=days, limit=page_size + 1, offset=offset
        )
    has_more, next_page, query_string, base_params = build_pagination_context(
        author, tag, days, page, page_size, len(essays)
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
async def editor(request: Request, d: str | None = None):
    require_user(request, require_signing=True)
    content = ""
    title = ""
    summary = ""
    tags = ""
    if d:
        async with get_session() as session:
            result = await session.execute(
                select(models.Essay).where(models.Essay.identifier == d)
            )
            essay = result.scalars().first()
            if essay:
                latest = await EssayService(session).latest_version(essay)
                if latest:
                    content = latest.content
                    title = essay.title
                    summary = latest.summary or ""
                    tags = latest.tags or ""
    context = {"request": request, "content": content, "title": title, "identifier": d, "summary": summary, "tags": tags}
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
):
    session_data = require_signing_session(request)
    signer = signer_from_session(session_data)
    parsed_tags = parse_tags_input(tags)

    async with get_session() as session:
        service = EssayService(session)
        author_pubkey = signer.get_public_key()

        if action == "draft":
            draft = await service.save_draft(identifier, title, content, summary, parsed_tags, author_pubkey=author_pubkey)
            return RedirectResponse(url=f"/essay/{draft.essay.identifier}", status_code=303)

        prepared = await service.prepare_publication(identifier, title, summary, author_pubkey)
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
        return RedirectResponse(url=f"/essay/{version.essay.identifier}", status_code=303)


@app.get("/essay/{identifier}", response_class=HTMLResponse)
async def essay_detail(request: Request, identifier: str, version: int | None = None):
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
    context = {
        "request": request,
        "essay": essay,
        "version": selected_version,
        "history": history,
    }
    return templates.TemplateResponse("essay_detail.html", context)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    require_user(request, allow_readonly=True)
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

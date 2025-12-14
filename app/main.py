import asyncio
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

from app.config import settings
from app.db import models
from app.db.session import aengine, get_session
from app.indexer import run_indexer
from app.nostr.key import NostrKeyError, encode_npub, npub_from_secret
from app.services.essays import EssayService

app = FastAPI(title="Imprint", version="0.1.0")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

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


def parse_tags_input(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


async def init_models():
    async with aengine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)


@app.on_event("startup")
async def startup_event():
    await init_models()
    global indexer_task
    if settings.relay_urls:
        indexer_task = asyncio.create_task(run_indexer(get_session, settings.relay_urls))


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
    async with get_session() as session:
        service = EssayService(session)
        essays = await service.list_latest_published(author=author, tag=tag, days=days, limit=12)
    context = {
        "request": request,
        "essays": essays,
        "filters": {"author": author or "", "days": days or "", "tag": tag or ""},
        "npub": get_npub(),
    }
    return templates.TemplateResponse("index.html", context)


@app.get("/partials/recent", response_class=HTMLResponse)
async def recent_fragment(request: Request, author: str | None = None, days: int | None = None, tag: str | None = None):
    async with get_session() as session:
        service = EssayService(session)
        essays = await service.list_latest_published(author=author, tag=tag, days=days, limit=12)
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
    page_size = 12
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
    page_size = 12
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
):
    try:
        async with get_session() as session:
            service = EssayService(session)
            parsed_tags = parse_tags_input(tags)
            if action == "draft":
                draft = await service.save_draft(identifier, title, content, summary, parsed_tags)
                return RedirectResponse(url=f"/essay/{draft.essay.identifier}", status_code=303)
            version = await service.publish(identifier, title, content, summary, parsed_tags)
            return RedirectResponse(url=f"/essay/{version.essay.identifier}", status_code=303)
    except NostrKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    async with get_session() as session:
        relays = (await session.execute(select(models.Relay))).scalars().all()
    npub = get_npub()
    context = {"request": request, "relays": relays, "npub": npub}
    return templates.TemplateResponse("settings.html", context)


@app.post("/settings/relays")
async def add_relay(request: Request, relay_url: str = Form(...)):
    async with get_session() as session:
        existing = await session.execute(select(models.Relay).where(models.Relay.url == relay_url))
        relay = existing.scalars().first()
        if not relay:
            relay = models.Relay(url=relay_url)
            session.add(relay)
            await session.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/relays/{relay_id}/delete")
async def delete_relay(relay_id: int):
    async with get_session() as session:
        relay = await session.get(models.Relay, relay_id)
        if relay:
            await session.delete(relay)
            await session.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/test")
async def test_relay(relay_url: str = Form(...)):
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

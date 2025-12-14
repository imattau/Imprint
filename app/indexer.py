import asyncio
import datetime as dt
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.db import models
from app.nostr.relay import subscribe_long_form


async def store_event(session: AsyncSession, event: dict) -> None:
    tags = {tag[0]: tag[1] for tag in event.get("tags", []) if len(tag) >= 2}
    topics = [tag[1] for tag in event.get("tags", []) if len(tag) >= 2 and tag[0] == "t"]
    identifier = tags.get("d")
    title = tags.get("title")
    version = int(tags.get("version", "0"))
    status = tags.get("status", "published")
    summary = tags.get("summary")
    if not identifier or not title or len(event.get("content", "")) < 30:
        return
    result = await session.execute(select(models.Essay).where(models.Essay.identifier == identifier))
    essay = result.scalars().first()
    if not essay:
        essay = models.Essay(
            identifier=identifier,
            title=title,
            author_pubkey=event.get("pubkey"),
            summary=summary,
            tags=",".join(topics) if topics else None,
            latest_version=version,
            latest_event_id=event.get("id"),
            updated_at=dt.datetime.now(dt.timezone.utc),
        )
        session.add(essay)
        await session.flush()
    if version >= essay.latest_version:
        essay.title = title
        essay.latest_version = version
        essay.latest_event_id = event.get("id")
        essay.updated_at = dt.datetime.now(dt.timezone.utc)
        essay.tags = ",".join(topics) if topics else None

    exists = await session.execute(
        select(models.EssayVersion).where(
            models.EssayVersion.essay_id == essay.id,
            models.EssayVersion.version == version,
        )
    )
    if not exists.scalars().first():
        version_row = models.EssayVersion(
            essay_id=essay.id,
            version=version,
            content=event.get("content", ""),
            summary=summary,
            status=status,
            event_id=event.get("id"),
            supersedes_event_id=tags.get("supersedes"),
            published_at=dt.datetime.fromtimestamp(event.get("created_at", 0), dt.timezone.utc),
            tags=",".join(topics) if topics else None,
        )
        session.add(version_row)
    await session.commit()


async def index_relay(session_factory, relay_url: str):
    async with session_factory() as session:
        try:
            async for event in subscribe_long_form(relay_url, limit=100):
                await store_event(session, event)
        except Exception:
            pass


async def run_indexer(session_factory, relay_urls: List[str]):
    while True:
        tasks = []
        for relay_url in relay_urls:
            tasks.append(index_relay(session_factory, relay_url))
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(30)

import datetime as dt
import secrets
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.nostr.event import build_long_form_event
from app.nostr.key import derive_pubkey_hex, load_private_key
from app.nostr.relay import publish_event
from app.config import settings


class EssayService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_essay(self, identifier: Optional[str], title: str, author_pubkey: str, summary: Optional[str]):
        if identifier:
            result = await self.session.execute(select(models.Essay).where(models.Essay.identifier == identifier))
            essay = result.scalars().first()
        else:
            essay = None
        if not essay:
            identifier = identifier or secrets.token_hex(4)
            essay = models.Essay(identifier=identifier, title=title, author_pubkey=author_pubkey, summary=summary)
            self.session.add(essay)
            await self.session.flush()
        else:
            essay.title = title
            essay.summary = summary
        return essay

    async def latest_version(self, essay: models.Essay) -> Optional[models.EssayVersion]:
        result = await self.session.execute(
            select(models.EssayVersion)
            .where(models.EssayVersion.essay_id == essay.id)
            .order_by(models.EssayVersion.version.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def next_version(self, essay: models.Essay) -> int:
        latest = await self.latest_version(essay)
        return (latest.version if latest else 0) + 1

    async def save_draft(self, identifier: Optional[str], title: str, content: str, summary: Optional[str]) -> models.EssayVersion:
        sk = load_private_key(settings.nostr_secret)
        pubkey = derive_pubkey_hex(sk)
        essay = await self.get_or_create_essay(identifier, title, pubkey, summary)
        version_num = await self.next_version(essay)
        draft = models.EssayVersion(
            essay_id=essay.id,
            version=version_num,
            content=content,
            summary=summary,
            status="draft",
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        draft.essay = essay
        essay.latest_version = version_num
        self.session.add(draft)
        await self.session.commit()
        await self.session.refresh(draft)
        return draft

    async def publish(self, identifier: Optional[str], title: str, content: str, summary: Optional[str]) -> models.EssayVersion:
        sk = load_private_key(settings.nostr_secret)
        pubkey = derive_pubkey_hex(sk)
        essay = await self.get_or_create_essay(identifier, title, pubkey, summary)
        version_num = await self.next_version(essay)
        prev_version = await self.latest_version(essay)
        supersedes = prev_version.event_id if prev_version and prev_version.status == "published" else None

        event = build_long_form_event(
            sk=sk,
            pubkey=pubkey,
            identifier=essay.identifier,
            title=title,
            content=content,
            summary=summary,
            version=version_num,
            status="published",
            supersedes=supersedes,
        )

        version = models.EssayVersion(
            essay_id=essay.id,
            version=version_num,
            content=content,
            summary=summary,
            status="published",
            event_id=event["id"],
            supersedes_event_id=supersedes,
            published_at=dt.datetime.fromtimestamp(event["created_at"], dt.timezone.utc),
        )
        version.essay = essay
        essay.latest_version = version_num
        essay.latest_event_id = event["id"]
        essay.title = title
        essay.summary = summary
        self.session.add(version)
        await self.session.commit()

        for relay_url in settings.relay_urls:
            try:
                await publish_event(relay_url, event)
            except Exception:
                continue
        await self.session.refresh(version)
        return version

    async def list_recent(self, author: Optional[str] = None, topic: Optional[str] = None, days: int | None = None):
        query = select(models.Essay).order_by(models.Essay.updated_at.desc()).limit(50)
        if author:
            query = query.where(models.Essay.author_pubkey == author)
        if days:
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
            query = query.where(models.Essay.updated_at >= cutoff)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def fetch_history(self, identifier: str):
        result = await self.session.execute(
            select(models.EssayVersion)
            .join(models.Essay)
            .where(models.Essay.identifier == identifier)
            .order_by(models.EssayVersion.version.desc())
        )
        return result.scalars().all()

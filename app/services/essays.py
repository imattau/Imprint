import datetime as dt
import re
import secrets
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import models
from app.nostr.event import ensure_imprint_tag
from app.nostr.key import NostrKeyError, decode_nip19
from app.config import settings
from app.nostr.relay_client import relay_client
# Backwards-compatible alias for tests that monkeypatch publish_event.
publish_event = relay_client.publish_event


class EssayService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_essay_by_identifier(self, identifier: str) -> models.Essay | None:
        result = await self.session.execute(select(models.Essay).where(models.Essay.identifier == identifier))
        return result.scalars().first()

    async def ensure_identifier_available(self, identifier: Optional[str], author_pubkey: str) -> models.Essay | None:
        if not identifier:
            return None
        existing = await self.find_essay_by_identifier(identifier)
        if existing and existing.author_pubkey != author_pubkey:
            raise PermissionError("Identifier belongs to another author")
        return existing

    async def get_or_create_essay(
        self, identifier: Optional[str], title: str, author_pubkey: str, summary: Optional[str]
    ) -> models.Essay:
        essay = await self.ensure_identifier_available(identifier, author_pubkey)
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

    async def save_draft(
        self,
        identifier: Optional[str],
        title: str,
        content: str,
        summary: Optional[str],
        tags: Optional[list[str]] = None,
        author_pubkey: str | None = None,
        draft_id: int | None = None,
    ) -> models.Draft:
        if not author_pubkey:
            raise NostrKeyError("Author key required")

        tags_string = ",".join(tags) if tags else None
        now = dt.datetime.now(dt.timezone.utc)
        if identifier:
            await self.ensure_identifier_available(identifier, author_pubkey)
        draft: models.Draft | None = None
        if draft_id:
            draft = await self.session.get(models.Draft, draft_id)
            if draft and draft.author_pubkey != author_pubkey:
                raise NostrKeyError("Unauthorized draft access")
        if draft:
            draft.title = title
            draft.content = content
            draft.summary = summary
            draft.identifier = identifier or draft.identifier
            draft.tags = tags_string
            draft.updated_at = now
        else:
            draft = models.Draft(
                author_pubkey=author_pubkey,
                title=title,
                content=content,
                summary=summary,
                identifier=identifier,
                tags=tags_string,
                created_at=now,
                updated_at=now,
            )
            self.session.add(draft)
        await self.session.commit()
        await self.session.refresh(draft)
        return draft

    async def list_drafts(self, author_pubkey: str):
        query = (
            select(models.Draft)
            .where(models.Draft.author_pubkey == author_pubkey)
            .where(models.Draft.published_event_id.is_(None))
            .order_by(models.Draft.updated_at.desc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_draft(self, draft_id: int, author_pubkey: str) -> models.Draft | None:
        return await self.session.get(models.Draft, draft_id)

    async def delete_draft(self, draft_id: int, author_pubkey: str) -> None:
        draft = await self.get_draft(draft_id, author_pubkey)
        if draft:
            await self.session.delete(draft)
            await self.session.commit()

    async def mark_draft_published(self, draft: models.Draft, event_id: str) -> None:
        draft.published_event_id = event_id
        draft.updated_at = dt.datetime.now(dt.timezone.utc)
        await self.session.commit()

    async def prepare_publication(
        self, identifier: Optional[str], title: str, summary: Optional[str], author_pubkey: str
    ) -> tuple[models.Essay, int, Optional[str]]:
        essay = await self.get_or_create_essay(identifier, title, author_pubkey, summary)
        version_num = await self.next_version(essay)
        prev_version = await self.latest_version(essay)
        supersedes = prev_version.event_id if prev_version and prev_version.status == "published" else None
        return essay, version_num, supersedes

    async def publish(
        self,
        identifier: Optional[str],
        title: str,
        content: str,
        summary: Optional[str],
        tags: Optional[list[str]],
        signed_event: dict,
        relay_urls: Optional[list[str]] = None,
        prepared: tuple[models.Essay, int, Optional[str]] | None = None,
    ) -> models.EssayVersion:
        topics = ensure_imprint_tag(tags)
        pubkey = signed_event.get("pubkey")
        essay: models.Essay
        version_num: int
        supersedes: Optional[str]
        if prepared:
            essay, version_num, supersedes = prepared
        else:
            essay, version_num, supersedes = await self.prepare_publication(identifier, title, summary, pubkey)

        # Ensure essay is attached to this session.
        essay = await self.session.get(models.Essay, essay.id)

        version = models.EssayVersion(
            essay_id=essay.id,
            version=version_num,
            content=content,
            summary=summary,
            tags=",".join(topics) if topics else None,
            status="published",
            event_id=signed_event.get("id"),
            supersedes_event_id=supersedes,
            published_at=dt.datetime.fromtimestamp(signed_event.get("created_at"), dt.timezone.utc),
        )
        version.essay = essay
        essay.latest_version = version_num
        essay.latest_event_id = signed_event.get("id")
        essay.title = title
        essay.summary = summary
        essay.tags = ",".join(topics) if topics else None
        self.session.add(version)
        await self.session.commit()

        target_relays = relay_urls if relay_urls is not None else settings.relay_urls
        await relay_client.publish_event(signed_event, target_relays)
        await self.session.refresh(version)
        return version

    async def list_latest_published(
        self,
        author: Optional[str] = None,
        tag: Optional[str] = None,
        days: int | None = None,
        limit: int = 15,
        offset: int = 0,
        imprint_only: bool = False,
    ):
        subquery = (
            select(
                models.EssayVersion.essay_id,
                func.max(models.EssayVersion.version).label("max_version"),
            )
            .where(models.EssayVersion.status == "published")
            .group_by(models.EssayVersion.essay_id)
        ).subquery()

        query = (
            select(models.EssayVersion)
            .join(
                subquery,
                (models.EssayVersion.essay_id == subquery.c.essay_id)
                & (models.EssayVersion.version == subquery.c.max_version),
            )
            .join(models.Essay)
            .options(selectinload(models.EssayVersion.essay))
        )

        author_hex = author
        if author and author.startswith("npub"):
            try:
                author_hex = decode_nip19(author)
            except NostrKeyError:
                author_hex = None
        if author_hex:
            query = query.where(models.Essay.author_pubkey == author_hex)
        if days is not None and days > 0:
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
            query = query.where(models.EssayVersion.published_at >= cutoff)
        if tag:
            # Allow comma or whitespace separated tag filters; match any token.
            tokens = [t for t in re.split("[\\s,]+", tag.lower()) if t]
            if tokens:
                conditions = [models.EssayVersion.tags.ilike(f"%{token}%") for token in tokens]
                query = query.where(or_(*conditions))
        if imprint_only:
            query = query.where(models.EssayVersion.tags.ilike("%imprint%"))
        query = query.order_by(models.EssayVersion.published_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(query)
        return result.scalars().unique().all()

    async def fetch_history(self, identifier: str):
        result = await self.session.execute(
            select(models.EssayVersion)
            .join(models.Essay)
            .where(models.Essay.identifier == identifier)
            .order_by(models.EssayVersion.version.desc())
        )
        return result.scalars().all()

    async def find_version_by_event_id(self, event_id: str) -> Optional[models.EssayVersion]:
        result = await self.session.execute(
            select(models.EssayVersion)
            .options(selectinload(models.EssayVersion.essay))
            .where(models.EssayVersion.event_id == event_id)
        )
        return result.scalars().first()

    async def list_history_for_author(self, author_pubkey: str, limit: int = 50):
        """All published versions for author (not deduped)."""

        query = (
            select(models.EssayVersion)
            .join(models.Essay)
            .where(models.Essay.author_pubkey == author_pubkey)
            .where(models.EssayVersion.status == "published")
            .order_by(models.EssayVersion.published_at.desc().nullslast())
            .limit(limit)
            .options(selectinload(models.EssayVersion.essay))
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def list_latest_history_for_author(self, author_pubkey: str, limit: int = 50):
        """Latest published version per identifier for author."""

        # Use simple python grouping to avoid dialect-specific SQL issues.
        versions = await self.list_history_for_author(author_pubkey, limit=limit * 5)
        latest: dict[str, models.EssayVersion] = {}
        counts: dict[str, int] = {}
        for v in versions:
            ident = v.essay.identifier
            counts[ident] = counts.get(ident, 0) + 1
            if ident not in latest or (v.published_at and latest[ident].published_at and v.published_at > latest[ident].published_at):
                latest[ident] = v
        # Preserve order by published_at desc.
        ordered = sorted(latest.values(), key=lambda v: v.published_at or dt.datetime.min, reverse=True)
        revision_counts = {v.essay.id: counts[v.essay.identifier] for v in ordered}
        return ordered[:limit], revision_counts

    async def list_revisions_for_identifier(self, author_pubkey: str, identifier: str):
        """All revisions for an identifier, newest first."""

        query = (
            select(models.EssayVersion)
            .join(models.Essay)
            .where(models.Essay.author_pubkey == author_pubkey)
            .where(models.Essay.identifier == identifier)
            .where(models.EssayVersion.status == "published")
            .order_by(models.EssayVersion.published_at.desc().nullslast(), models.EssayVersion.version.desc())
            .options(selectinload(models.EssayVersion.essay))
        )
        result = await self.session.execute(query)
        return result.scalars().all()

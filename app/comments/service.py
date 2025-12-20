import datetime as dt
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import delete, select

from app.auth.schemas import SessionData, SessionMode
from app.db import models
from app.nostr.event import compute_event_id, serialize_event
from app.nostr.relay_client import relay_client

logger = logging.getLogger(__name__)


@dataclass
class Comment:
    id: str
    pubkey: str
    content: str
    created_at: int
    parent_id: Optional[str]
    root_id: str
    tags: list[list[str]]
    deleted: bool = False
    replies: list["Comment"] = field(default_factory=list)


class CommentCache:
    def __init__(self, ttl: int = 30):
        self.ttl = ttl
        self._store: dict[tuple[str, str | None], tuple[float, Any]] = {}

    def get(self, key: tuple[str, str | None]) -> Any:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: tuple[str, str | None], value: Any) -> None:
        self._store[key] = (time.time() + self.ttl, value)

    def invalidate(self, prefix: str) -> None:
        for k in list(self._store.keys()):
            if k[0] == prefix:
                self._store.pop(k, None)

    def invalidate_viewer(self, viewer_pubkey: str | None) -> None:
        if viewer_pubkey is None:
            return
        for k in list(self._store.keys()):
            if k[1] == viewer_pubkey:
                self._store.pop(k, None)


class CommentService:
    def __init__(self, session, cache: Optional[CommentCache] = None, local_cache_ttl_seconds: int = 600):
        self.session = session
        self.cache = cache or CommentCache()
        self.local_cache_ttl = dt.timedelta(seconds=local_cache_ttl_seconds)

    async def _blocked_pubkeys(self, viewer_pubkey: str | None) -> set[str]:
        if not viewer_pubkey:
            return set()
        result = await self.session.execute(
            models.UserBlock.__table__.select().where(models.UserBlock.owner_pubkey == viewer_pubkey)
        )
        return {row.blocked_pubkey for row in result}

    async def _load_local_cache(self, root_id: str) -> list[dict[str, Any]]:
        cutoff = dt.datetime.now(dt.timezone.utc) - self.local_cache_ttl
        result = await self.session.execute(
            select(models.CommentCache).where(models.CommentCache.root_id == root_id).where(models.CommentCache.created_at >= cutoff)
        )
        rows = result.scalars().all()
        events: list[dict[str, Any]] = []
        for row in rows:
            try:
                events.append(json.loads(row.event_json))
            except Exception:
                continue
        return events

    async def _store_local_cache(self, root_id: str, event: dict) -> None:
        row = models.CommentCache(root_id=root_id, event_id=event.get("id") or "", event_json=json.dumps(event))
        self.session.add(row)
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()

    async def fetch_comments_for_essay(
        self,
        root_event_id: str,
        relays: Iterable[str],
        related_event_ids: Optional[list[str]] = None,
        limit: int = 100,
        until: Optional[int] = None,
        viewer_pubkey: Optional[str] = None,
    ) -> list[Comment]:
        cache_key = (root_event_id, viewer_pubkey)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.debug("comments cache hit for root=%s viewer=%s", root_event_id, viewer_pubkey)
            return cached

        relays_list = list(relays)[:5]
        start = time.time()
        filters: list[dict[str, Any]] = [{"kinds": [1], "#e": related_event_ids or [root_event_id], "limit": limit}]
        if until:
            filters[0]["until"] = until

        relay_events: list[dict[str, Any]] = []
        if relays_list:
            relay_events = await relay_client.fetch_events(filters, relays_list, timeout_seconds=3)
        local_events = await self._load_local_cache(root_event_id)

        events_by_id: dict[str, dict[str, Any]] = {}
        for ev in relay_events + local_events:
            cid = ev.get("id")
            if not cid or cid in events_by_id:
                continue
            events_by_id[cid] = ev

        comment_ids = list(events_by_id.keys())
        deleted_ids: set[str] = set()
        if comment_ids and relays_list:
            deletions = await relay_client.fetch_events(
                [{"kinds": [5], "#e": comment_ids}], relays_list, timeout_seconds=3
            )
            for d in deletions:
                for tag in d.get("tags", []):
                    if tag and tag[0] == "e" and len(tag) > 1:
                        deleted_ids.add(tag[1])
                        await self.session.execute(
                            delete(models.CommentCache).where(models.CommentCache.event_id == tag[1]).execution_options(synchronize_session="fetch")
                        )
            if deleted_ids:
                try:
                    await self.session.commit()
                except Exception:
                    await self.session.rollback()

        blocked = await self._blocked_pubkeys(viewer_pubkey)

        comments: dict[str, Comment] = {}
        children: dict[str, list[Comment]] = defaultdict(list)
        for ev in events_by_id.values():
            cid = ev.get("id")
            if not cid:
                continue
            pubkey = ev.get("pubkey", "")
            if pubkey in blocked:
                continue
            parent_id = None
            root_id = root_event_id
            for tag in ev.get("tags", []):
                if len(tag) >= 4 and tag[0] == "e":
                    if tag[3] == "reply":
                        parent_id = tag[1]
                    if tag[3] == "root":
                        root_id = tag[1]
            comment = Comment(
                id=cid,
                pubkey=pubkey,
                content=ev.get("content", ""),
                created_at=int(ev.get("created_at", 0)),
                parent_id=parent_id,
                root_id=root_id,
                tags=ev.get("tags", []),
                deleted=cid in deleted_ids,
            )
            comments[cid] = comment
            if parent_id:
                children[parent_id].append(comment)

        for cid, comment in comments.items():
            if cid in children:
                comment.replies = sorted(children[cid], key=lambda c: c.created_at)

        roots = [c for c in comments.values() if c.parent_id is None]
        roots.sort(key=lambda c: c.created_at)
        elapsed = (time.time() - start) * 1000
        logger.debug(
            "comments fetched root=%s viewer=%s count=%s blocked=%s elapsed_ms=%.1f",
            root_event_id,
            viewer_pubkey,
            len(roots),
            len(blocked),
            elapsed,
        )
        self.cache.set(cache_key, roots)
        return roots

    async def publish_comment(
        self,
        essay_version: models.EssayVersion,
        content: str,
        signer_session: SessionData,
        parent_id: Optional[str] = None,
        relays: Optional[Iterable[str]] = None,
        root_id: Optional[str] = None,
    ) -> dict:
        if signer_session.session_mode == SessionMode.readonly:
            raise PermissionError("Signer session required")
        pubkey = signer_session.pubkey_hex or ""
        created_at = int(time.time())
        comment_root_id = root_id or essay_version.event_id
        tags: list[list[str]] = [
            ["e", comment_root_id, "", "root"],
            ["p", essay_version.essay.author_pubkey or ""],
        ]
        if parent_id:
            tags.append(["e", parent_id, "", "reply"])
        template = {
            "pubkey": pubkey,
            "created_at": created_at,
            "kind": 1,
            "tags": tags,
            "content": content,
        }
        # Prepare and sign event via signer session
        from app.nostr.signers import signer_from_session

        signer = signer_from_session(signer_session)
        serialized = serialize_event(pubkey, created_at, 1, tags, content)
        event_id = compute_event_id(serialized)
        template["id"] = event_id
        signed = await signer.sign_event(template)
        await relay_client.publish_event(signed, relays=relays or [])
        try:
            await self._store_local_cache(comment_root_id, signed)
        finally:
            self.cache.invalidate(comment_root_id)
        logger.debug(
            "comment published root=%s parent=%s relays=%s",
            comment_root_id,
            parent_id,
            len(list(relays or [])),
        )
        return signed

    async def delete_comment(
        self, comment_id: str, signer_session: SessionData, root_id: str, relays: Optional[Iterable[str]] = None
    ) -> dict:
        if signer_session.session_mode == SessionMode.readonly:
            raise PermissionError("Signer session required")
        template = {
            "pubkey": signer_session.pubkey_hex or "",
            "created_at": int(time.time()),
            "kind": 5,
            "tags": [["e", comment_id]],
            "content": "",
        }
        from app.nostr.signers import signer_from_session

        signer = signer_from_session(signer_session)
        serialized = serialize_event(template["pubkey"], template["created_at"], template["kind"], template["tags"], "")
        template["id"] = compute_event_id(serialized)
        signed = await signer.sign_event(template)
        await relay_client.publish_event(signed, relays=relays or [])
        await self.session.execute(delete(models.CommentCache).where(models.CommentCache.event_id == comment_id))
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
        self.cache.invalidate(root_id)
        logger.debug("comment deleted id=%s root=%s relays=%s", comment_id, root_id, len(list(relays or [])))
        return signed

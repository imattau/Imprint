import datetime as dt
import json
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models


class AdminEventService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def add_event(
        self,
        action: str,
        level: str,
        message: str,
        actor_pubkey: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> models.AdminEvent:
        event = models.AdminEvent(
            created_at=dt.datetime.now(dt.timezone.utc),
            level=level,
            action=action,
            actor_pubkey=actor_pubkey,
            message=message,
            metadata_json=json.dumps(metadata, sort_keys=True) if metadata else None,
        )
        self.session.add(event)
        return event

    async def log_event(
        self,
        action: str,
        level: str,
        message: str,
        actor_pubkey: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> models.AdminEvent:
        event = self.add_event(action, level, message, actor_pubkey=actor_pubkey, metadata=metadata)
        await self.session.commit()
        await self.session.refresh(event)
        return event

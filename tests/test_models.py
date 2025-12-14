import datetime as dt
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import models


@pytest.mark.asyncio
async def test_model_relationships(session):
    essay = models.Essay(identifier="abc", title="Title", author_pubkey="pub", summary="sum")
    session.add(essay)
    await session.flush()
    version = models.EssayVersion(
        essay_id=essay.id,
        version=1,
        content="content",
        summary="sum",
        status="published",
        published_at=dt.datetime.now(dt.timezone.utc),
    )
    version.essay = essay
    session.add(version)
    await session.commit()

    result = await session.execute(
        select(models.Essay).options(selectinload(models.Essay.versions)).where(models.Essay.id == essay.id)
    )
    fetched = result.scalars().first()
    assert fetched is not None
    assert fetched.versions[0].essay_id == fetched.id

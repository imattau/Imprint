import datetime as dt

import pytest

from app.db import models
from app.nostr.key import encode_npub
from app.services.essays import EssayService


@pytest.mark.asyncio
async def test_latest_version_per_identifier(session):
    essay = models.Essay(identifier="essay-a", title="First", author_pubkey="a" * 64)
    session.add(essay)
    await session.flush()

    older = models.EssayVersion(
        essay_id=essay.id,
        version=1,
        status="published",
        published_at=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
    )
    newer = models.EssayVersion(
        essay_id=essay.id,
        version=2,
        status="published",
        published_at=dt.datetime(2024, 2, 1, tzinfo=dt.timezone.utc),
    )
    session.add_all([older, newer])
    await session.commit()

    service = EssayService(session)
    results = await service.list_latest_published()

    assert len(results) == 1
    assert results[0].version == 2
    assert results[0].essay.identifier == "essay-a"


@pytest.mark.asyncio
async def test_sorted_by_published_date_desc(session):
    essay1 = models.Essay(identifier="first", title="First", author_pubkey="b" * 64)
    essay2 = models.Essay(identifier="second", title="Second", author_pubkey="c" * 64)
    session.add_all([essay1, essay2])
    await session.flush()

    first_version = models.EssayVersion(
        essay_id=essay1.id,
        version=1,
        status="published",
        published_at=dt.datetime(2023, 12, 1, tzinfo=dt.timezone.utc),
    )
    second_version = models.EssayVersion(
        essay_id=essay2.id,
        version=1,
        status="published",
        published_at=dt.datetime(2024, 3, 1, tzinfo=dt.timezone.utc),
    )
    session.add_all([first_version, second_version])
    await session.commit()

    service = EssayService(session)
    results = await service.list_latest_published()

    assert [r.essay.identifier for r in results] == ["second", "first"]


@pytest.mark.asyncio
async def test_author_and_tag_filtering(session):
    author_hex = "d" * 64
    other_hex = "e" * 64
    essay1 = models.Essay(identifier="authored", title="Tagged", author_pubkey=author_hex)
    essay2 = models.Essay(identifier="other", title="Other", author_pubkey=other_hex)
    session.add_all([essay1, essay2])
    await session.flush()

    version1 = models.EssayVersion(
        essay_id=essay1.id,
        version=1,
        status="published",
        tags="nostr,writing",
        published_at=dt.datetime(2024, 3, 2, tzinfo=dt.timezone.utc),
    )
    version2 = models.EssayVersion(
        essay_id=essay2.id,
        version=1,
        status="published",
        tags="travel",
        published_at=dt.datetime(2024, 3, 3, tzinfo=dt.timezone.utc),
    )
    session.add_all([version1, version2])
    await session.commit()

    service = EssayService(session)

    npub = encode_npub(author_hex)
    author_filtered = await service.list_latest_published(author=npub)
    assert len(author_filtered) == 1
    assert author_filtered[0].essay.identifier == "authored"

    tag_filtered = await service.list_latest_published(tag="writing")
    assert len(tag_filtered) == 1
    assert tag_filtered[0].essay.identifier == "authored"

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.db import models
from app.db import session as db_session
from app import main


@pytest_asyncio.fixture()
async def session():
    # Ensure schema exists for ad-hoc session tests.
    await main.init_models()
    engine = db_session.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with AsyncSessionLocal() as session:
        yield session

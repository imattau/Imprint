import os
from contextlib import asynccontextmanager
from asyncio import BaseEventLoop
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Some environments hang when the default executor is lazily created.
# Force a shared executor for all run_in_executor calls.
_shared_executor = ThreadPoolExecutor()
_original_run_in_executor = BaseEventLoop.run_in_executor


def _patched_run_in_executor(self, executor, func, *args):
    if executor is None:
        executor = _shared_executor
    return _original_run_in_executor(self, executor, func, *args)


if getattr(BaseEventLoop.run_in_executor, "_imprint_patched", False) is False:
    BaseEventLoop.run_in_executor = _patched_run_in_executor
    BaseEventLoop.run_in_executor._imprint_patched = True

# Use an in-memory database during tests unless overridden, to keep test runs fast and isolated.
DEFAULT_DB = "sqlite+aiosqlite:///./imprint.db"
if os.getenv("PYTEST_CURRENT_TEST"):
    DEFAULT_DB = "sqlite+aiosqlite:///:memory:"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB)

aengine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = sessionmaker(bind=aengine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_session():
    session: AsyncSession = AsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()

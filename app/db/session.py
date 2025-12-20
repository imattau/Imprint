import os
import re
from contextlib import asynccontextmanager
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session


DEFAULT_DB_URL = "sqlite:///./imprint.db"


def resolve_database_url() -> str:
    """
    Resolve the effective database URL, forcing an isolated per-test sqlite database.
    In test runs we use an in-memory sqlite URL keyed by the node id so each test gets
    a fresh database without touching any dev/prod files.
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        nodeid = os.getenv("PYTEST_CURRENT_TEST", "test").split(" ")[0]
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid)
        return os.getenv("TEST_DATABASE_URL", f"sqlite:///:memory:?cache=shared&imprint_test={safe}")
    return os.getenv("DATABASE_URL", DEFAULT_DB_URL)


_ENGINE_CACHE: Dict[str, Session] = {}
SessionLocal = None
AsyncSessionLocal = None  # compatibility for tests that monkeypatch this attribute
_CURRENT_SESSION_URL: str | None = None


def _normalize_url(url: str) -> str:
    return url.replace("+aiosqlite", "")


def _make_engine(url: str):
    engine_kwargs = {"echo": False, "future": True}
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in url or "mode=memory" in url:
            connect_args["uri"] = True
    return create_engine(url, connect_args=connect_args, **engine_kwargs)


def get_engine(url: str | None = None):
    url = _normalize_url(url or resolve_database_url())
    if url not in _ENGINE_CACHE:
        _ENGINE_CACHE[url] = _make_engine(url)
    return _ENGINE_CACHE[url]


class AsyncSessionProxy:
    """Lightweight async-compatible wrapper around a synchronous Session."""

    def __init__(self, session: Session):
        self._session = session

    @property
    def bind(self):
        return self._session.bind

    async def execute(self, *args, **kwargs):
        return await asyncio.to_thread(self._session.execute, *args, **kwargs)

    async def scalars(self, *args, **kwargs):
        return await asyncio.to_thread(self._session.scalars, *args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return await asyncio.to_thread(self._session.scalar, *args, **kwargs)

    async def commit(self):
        return await asyncio.to_thread(self._session.commit)

    async def rollback(self):
        return await asyncio.to_thread(self._session.rollback)

    async def flush(self):
        return await asyncio.to_thread(self._session.flush)

    async def refresh(self, instance):
        return await asyncio.to_thread(self._session.refresh, instance)

    async def get(self, *args, **kwargs):
        return await asyncio.to_thread(self._session.get, *args, **kwargs)

    async def close(self):
        return await asyncio.to_thread(self._session.close)

    async def delete(self, instance):
        return await asyncio.to_thread(self._session.delete, instance)

    def add(self, instance):
        return self._session.add(instance)

    def add_all(self, instances):
        return self._session.add_all(instances)

    def expire_all(self):
        return self._session.expire_all()


def _session_factory(url: str | None = None):
    global SessionLocal, AsyncSessionLocal, _CURRENT_SESSION_URL
    url = _normalize_url(url or resolve_database_url())
    if SessionLocal is None or _CURRENT_SESSION_URL != url:
        engine = get_engine(url)
        SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
        AsyncSessionLocal = SessionLocal
        _CURRENT_SESSION_URL = url
    return SessionLocal


def _ensure_default_executor():
    loop = asyncio.get_running_loop()
    if getattr(loop, "_default_executor", None) is None:
        loop.set_default_executor(ThreadPoolExecutor())


@asynccontextmanager
async def get_session():
    _ensure_default_executor()
    factory = _session_factory()
    session = AsyncSessionProxy(factory())
    try:
        yield session
    finally:
        await session.close()

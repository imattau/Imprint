import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def ensure_instance_settings_schema(engine: AsyncEngine) -> None:
    """
    Lightweight, idempotent SQLite schema upgrade for instance_settings.
    Adds missing columns without destructive changes.
    """
    async with engine.begin() as conn:
        tables = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='instance_settings'"))
        if not tables.fetchone():
            # Table does not exist; let create_all handle it elsewhere.
            return
        result = await conn.execute(text("PRAGMA table_info(instance_settings)"))
        columns = {row[1] for row in result.fetchall()}  # row[1] is column name

        missing: list[str] = []
        if "admin_allowlist" not in columns:
            missing.append("admin_allowlist")
        if "blocked_pubkeys" not in columns:
            missing.append("blocked_pubkeys")
        if "filter_recently_published_to_imprint_only" not in columns:
            missing.append("filter_recently_published_to_imprint_only")

        for col in missing:
            logger.info("Adding missing column to instance_settings: %s", col)
            if col in {"admin_allowlist", "blocked_pubkeys"}:
                await conn.execute(text(f"ALTER TABLE instance_settings ADD COLUMN {col} TEXT"))
            elif col == "filter_recently_published_to_imprint_only":
                await conn.execute(
                    text("ALTER TABLE instance_settings ADD COLUMN filter_recently_published_to_imprint_only BOOLEAN DEFAULT 0")
                )


async def ensure_admin_events_schema(engine: AsyncEngine) -> None:
    """
    Ensure the admin_events table exists for audit logging.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS admin_events ("
                "id INTEGER PRIMARY KEY, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                "level TEXT NOT NULL, "
                "action TEXT NOT NULL, "
                "actor_pubkey TEXT, "
                "message TEXT NOT NULL, "
                "metadata_json TEXT"
                ")"
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_admin_events_created_at ON admin_events(created_at)"))


def ensure_instance_settings_schema_sync(engine) -> None:
    """
    Synchronous variant for environments where async engine setup is slow or unavailable.
    """
    with engine.begin() as conn:
        tables = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='instance_settings'")
        if not tables.fetchone():
            return
        result = conn.exec_driver_sql("PRAGMA table_info(instance_settings)")
        columns = {row[1] for row in result.fetchall()}
        missing: list[str] = []
        if "admin_allowlist" not in columns:
            missing.append("admin_allowlist")
        if "blocked_pubkeys" not in columns:
            missing.append("blocked_pubkeys")
        if "filter_recently_published_to_imprint_only" not in columns:
            missing.append("filter_recently_published_to_imprint_only")
        for col in missing:
            if col in {"admin_allowlist", "blocked_pubkeys"}:
                conn.exec_driver_sql(f"ALTER TABLE instance_settings ADD COLUMN {col} TEXT")
            elif col == "filter_recently_published_to_imprint_only":
                conn.exec_driver_sql(
                    "ALTER TABLE instance_settings ADD COLUMN filter_recently_published_to_imprint_only BOOLEAN DEFAULT 0"
                )


def ensure_admin_events_schema_sync(engine) -> None:
    """
    Synchronous variant for ensuring admin_events exists.
    """
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS admin_events ("
            "id INTEGER PRIMARY KEY, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "level TEXT NOT NULL, "
            "action TEXT NOT NULL, "
            "actor_pubkey TEXT, "
            "message TEXT NOT NULL, "
            "metadata_json TEXT"
            ")"
        )
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_admin_events_created_at ON admin_events(created_at)")

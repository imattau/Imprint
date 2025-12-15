import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.schema_upgrade import ensure_instance_settings_schema
from app.main import app


@pytest.mark.asyncio
async def test_admin_page_after_schema_upgrade(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "old.db"
        # Create old schema missing admin_allowlist
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE instance_settings (
                id INTEGER PRIMARY KEY,
                site_name VARCHAR(120),
                site_tagline VARCHAR(255),
                site_description TEXT,
                public_base_url VARCHAR(255),
                default_relays TEXT,
                instance_nostr_address VARCHAR(255),
                instance_admin_npub VARCHAR(128),
                instance_admin_pubkey VARCHAR(128),
                lightning_address VARCHAR(255),
                donation_message VARCHAR(255),
                enable_payments BOOLEAN,
                enable_public_essays_feed BOOLEAN,
                enable_registrationless_readonly BOOLEAN,
                max_feed_items INTEGER,
                session_default_minutes INTEGER,
                theme_accent VARCHAR(16),
                updated_at DATETIME,
                updated_by_pubkey VARCHAR(128)
            )
            """
        )
        conn.commit()
        conn.close()

        database_url = f"sqlite+aiosqlite:///{db_path}"
        engine: AsyncEngine = create_async_engine(database_url)
        AsyncSessionLocal = sessionmaker(engine, class_=db_session.AsyncSessionLocal.class_, expire_on_commit=False)

        monkeypatch.setattr(db_session, "aengine", engine)
        monkeypatch.setattr(db_session, "AsyncSessionLocal", AsyncSessionLocal)

        await ensure_instance_settings_schema(engine)

        with TestClient(app) as client:
            resp = client.get("/admin", allow_redirects=True)
            assert resp.status_code == 200

        # Verify new columns exist after upgrade
        conn = sqlite3.connect(db_path)
        result = conn.execute("PRAGMA table_info(instance_settings)")
        cols = {row[1] for row in result.fetchall()}
        assert "admin_allowlist" in cols
        assert "blocked_pubkeys" in cols
        assert "filter_recently_published_to_imprint_only" in cols
        conn.close()

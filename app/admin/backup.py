import datetime as dt
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from typing import Tuple

from sqlalchemy.engine.url import make_url

from app.admin.service import InstanceSettingsService
from app.db import session as db_session


def _db_file_path() -> str | None:
    url = make_url(str(db_session.aengine.url))
    return os.path.abspath(url.database) if url.database else None


def _vacuum_snapshot(db_path: str) -> bytes:
    # Create a consistent copy using VACUUM INTO when available (SQLite 3.27+)
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(f"VACUUM INTO '{tmp_path}'")
        conn.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def create_backup_archive(session) -> Tuple[io.BytesIO, str]:
    buffer = io.BytesIO()
    db_path = _db_file_path()
    settings_service = InstanceSettingsService(session)
    settings = await settings_service.get_settings()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if db_path and os.path.exists(db_path):
            try:
                snapshot = _vacuum_snapshot(db_path)
            except Exception:
                with open(db_path, "rb") as f:
                    snapshot = f.read()
            zf.writestr("database.sqlite3", snapshot)
        zf.writestr(
            "settings.json",
            json.dumps(
                {col.name: getattr(settings, col.name) for col in settings.__table__.columns},
                default=str,
                indent=2,
            ),
        )
        meta = {
            "created_at": dt.datetime.utcnow().isoformat() + "Z",
            "schema_version": 1,
            "format": "imprint-backup",
        }
        zf.writestr("meta.json", json.dumps(meta, indent=2))
    buffer.seek(0)
    name = f"imprint-backup-{dt.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    return buffer, name


def validate_backup_archive(content: bytes) -> Tuple[bool, str | None]:
    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
            names = set(zf.namelist())
            required = {"database.sqlite3", "settings.json", "meta.json"}
            missing = required - names
            if missing:
                return False, f"Missing files: {', '.join(missing)}"
    except Exception as exc:
        return False, str(exc)
    return True, None


async def apply_restore_from_archive(content: bytes) -> None:
    db_path = _db_file_path()
    if not db_path:
        return
    await db_session.aengine.dispose()
    with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
        data = zf.read("database.sqlite3")
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(data)
            os.replace(tmp_path, db_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    # Settings are contained within the restored DB; no additional steps needed here.

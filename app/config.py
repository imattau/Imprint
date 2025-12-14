import os
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv

load_dotenv()


def get_env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value else None


class Settings(BaseModel):
    database_url: str = get_env("DATABASE_URL") or "sqlite+aiosqlite:///./imprint.db"
    relay_urls: list[str] = []
    nostr_secret: str | None = get_env("NOSTR_NSEC") or get_env("NOSTR_SK_HEX")
    session_secret: str = get_env("SESSION_SECRET") or "change-me-session-key"
    session_cookie_name: str = get_env("SESSION_COOKIE_NAME") or "imprint_session"
    session_cookie_same_site: str = get_env("SESSION_COOKIE_SAMESITE") or "lax"
    session_cookie_max_age: int = int(get_env("SESSION_COOKIE_MAX_AGE") or 60 * 60 * 24)
    session_cookie_https_only: bool = (get_env("SESSION_COOKIE_SECURE") or "").lower() in {"1", "true", "yes", "on"}
    debug: bool = (get_env("DEBUG") or "").lower() in {"1", "true", "yes", "on"}
    enable_indexer: bool = (get_env("ENABLE_INDEXER") or "false").lower() in {"1", "true", "yes", "on"}
    nip46_default_relay: str = get_env("NIP46_RELAY") or "wss://relay.damus.io"
    admin_token: str | None = get_env("ADMIN_TOKEN")
    admin_npubs: list[str] = []


try:
    settings = Settings(
        relay_urls=[u for u in (get_env("NOSTR_RELAYS") or "wss://relay.damus.io,wss://nos.lol").split(",") if u],
        admin_npubs=[u for u in (get_env("ADMIN_NPUBS") or "").split(",") if u],
    )
except ValidationError:
    settings = Settings()

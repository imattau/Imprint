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


try:
    settings = Settings(
        relay_urls=[u for u in (get_env("NOSTR_RELAYS") or "wss://relay.damus.io,wss://nos.lol").split(",") if u],
    )
except ValidationError:
    settings = Settings()

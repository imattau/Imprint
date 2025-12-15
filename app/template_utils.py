import markdown2
from markupsafe import Markup

from app.nostr.key import encode_npub


def markdown_filter(text: str | None):
    return Markup(markdown2.markdown(text or ""))


def author_display(pubkey: str | None) -> str:
    if not pubkey:
        return "Unknown author"
    try:
        npub = encode_npub(pubkey)
    except Exception:
        npub = pubkey
    if len(npub) > 20:
        return f"{npub[:10]}…{npub[-4:]}"
    return npub


def tags_list(tags: str | None) -> list[str]:
    if not tags:
        return []
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def short_identity(value: str | None) -> str:
    """Shorten long identifiers (npub/pubkey) for display in chips."""

    if not value:
        return ""
    return value if len(value) <= 14 else f"{value[:6]}…{value[-6:]}"


def register_filters(templates) -> None:
    """Register shared Jinja filters on a Jinja2Templates instance."""

    templates.env.filters["markdown"] = markdown_filter
    templates.env.filters["author_display"] = author_display
    templates.env.filters["tags_list"] = tags_list
    templates.env.filters["short_identity"] = short_identity

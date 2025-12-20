import datetime as dt
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Draft(Base):
    __tablename__ = "drafts"

    id = Column(Integer, primary_key=True)
    author_pubkey = Column(String(128), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text)
    identifier = Column(String(255))
    tags = Column(Text)
    published_event_id = Column(String(128))
    created_at = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )


class Essay(Base):
    __tablename__ = "essays"
    id = Column(Integer, primary_key=True)
    identifier = Column(String(255), unique=True, index=True)
    title = Column(String(255))
    author_pubkey = Column(String(128), index=True)
    summary = Column(Text)
    tags = Column(Text)
    latest_version = Column(Integer, default=1)
    latest_event_id = Column(String(128))
    created_at = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at = Column(
        DateTime, default=lambda: dt.datetime.now(dt.timezone.utc), onupdate=lambda: dt.datetime.now(dt.timezone.utc)
    )

    versions = relationship(
        "EssayVersion",
        back_populates="essay",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class EssayVersion(Base):
    __tablename__ = "essay_versions"
    id = Column(Integer, primary_key=True)
    essay_id = Column(Integer, ForeignKey("essays.id", ondelete="CASCADE"))
    version = Column(Integer)
    content = Column(Text)
    summary = Column(Text)
    status = Column(String(50), default="draft")
    event_id = Column(String(128), index=True)
    supersedes_event_id = Column(String(128))
    published_at = Column(DateTime)
    tags = Column(Text)
    created_at = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))

    essay = relationship("Essay", back_populates="versions", lazy="joined")

    __table_args__ = (UniqueConstraint("essay_id", "version", name="uix_essay_version"),)


class Relay(Base):
    __tablename__ = "relays"
    id = Column(Integer, primary_key=True)
    url = Column(String(255), unique=True, index=True)
    status = Column(String(50), default="unknown")
    last_checked = Column(DateTime)
    created_at = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))


class UserRelay(Base):
    __tablename__ = "user_relays"
    id = Column(Integer, primary_key=True)
    owner_pubkey = Column(String(128), index=True, nullable=False)
    url = Column(String(255), nullable=False)
    status = Column(String(50), default="unknown")
    last_checked = Column(DateTime)
    created_at = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))

    __table_args__ = (UniqueConstraint("owner_pubkey", "url", name="uix_user_relays_owner_url"),)


class UserBlock(Base):
    __tablename__ = "user_blocks"

    id = Column(Integer, primary_key=True)
    owner_pubkey = Column(String(128), index=True, nullable=False)
    blocked_pubkey = Column(String(128), index=True, nullable=False)
    created_at = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))

    __table_args__ = (UniqueConstraint("owner_pubkey", "blocked_pubkey", name="uix_user_blocks_owner_blocked"),)


class CommentCache(Base):
    __tablename__ = "comment_cache"

    id = Column(Integer, primary_key=True)
    root_id = Column(String(128), index=True, nullable=False)
    event_id = Column(String(128), unique=True, nullable=False)
    event_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))


class AdminEvent(Base):
    __tablename__ = "admin_events"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc), index=True)
    level = Column(String(16), default="info", nullable=False)
    action = Column(String(64), nullable=False)
    actor_pubkey = Column(String(128))
    message = Column(Text, nullable=False)
    metadata_json = Column(Text)


class InstanceSettings(Base):
    __tablename__ = "instance_settings"

    id = Column(Integer, primary_key=True)
    site_name = Column(String(120), default="Imprint")
    site_tagline = Column(String(255))
    site_description = Column(Text)
    public_base_url = Column(String(255))
    default_relays = Column(Text)
    instance_nostr_address = Column(String(255))
    instance_admin_npub = Column(String(128))
    instance_admin_pubkey = Column(String(128))
    lightning_address = Column(String(255))
    donation_message = Column(String(255))
    enable_payments = Column(Boolean, default=False)
    enable_public_essays_feed = Column(Boolean, default=True)
    enable_registrationless_readonly = Column(Boolean, default=True)
    max_feed_items = Column(Integer, default=15)
    session_default_minutes = Column(Integer, default=60)
    theme_accent = Column(String(16))
    filter_recently_published_to_imprint_only = Column(Boolean, default=False)
    admin_allowlist = Column(Text)
    blocked_pubkeys = Column(Text)
    updated_at = Column(
        DateTime,
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )
    updated_by_pubkey = Column(String(128))

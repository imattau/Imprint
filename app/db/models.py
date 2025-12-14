import datetime as dt
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


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

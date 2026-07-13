"""News source and raw article models."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class NewsSource(db.Model):
    """Information source configuration."""
    __tablename__ = "news_sources"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(64), unique=True, nullable=False)
    display_name = db.Column(db.String(128), nullable=False)
    base_url = db.Column(db.String(512))
    source_type = db.Column(db.String(32), default="rss")  # rss / webpage / api
    credibility = db.Column(db.Float, default=0.5)
    is_active = db.Column(db.Boolean, default=True)
    is_system = db.Column(db.Boolean, default=False)  # True=preset, False=user-added
    created_by = db.Column(db.String(64), default="system")
    poll_interval = db.Column(db.Integer, default=3600)  # seconds
    tags_json = db.Column(db.JSON, default=list)
    config_json = db.Column(db.JSON, default=dict)
    last_fetch_at = db.Column(db.DateTime(timezone=True))
    last_fetch_status = db.Column(db.String(32))  # success / timeout / error
    last_fetch_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))


    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "base_url": self.base_url,
            "source_type": self.source_type,
            "credibility": self.credibility,
            "is_active": self.is_active,
            "is_system": self.is_system,
            "created_by": self.created_by,
            "poll_interval": self.poll_interval,
            "tags": self.tags_json or [],
            "config": self.config_json or {},
            "last_fetch_at": to_iso(self.last_fetch_at),
            "last_fetch_status": self.last_fetch_status,
            "last_fetch_count": self.last_fetch_count,
            "created_at": to_iso(self.created_at),
            "updated_at": to_iso(self.updated_at),
        }


class RawArticle(db.Model):
    """Raw article collected from a news source."""
    __tablename__ = "raw_articles"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = db.Column(db.String(36), db.ForeignKey("news_sources.id"), nullable=False)
    url = db.Column(db.String(1024), unique=True)
    title = db.Column(db.String(512))
    content = db.Column(db.Text)
    summary = db.Column(db.Text)
    published_at = db.Column(db.DateTime(timezone=True))
    fetched_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    content_hash = db.Column(db.String(64), index=True)
    raw_metadata = db.Column(db.JSON, default=dict)
    processed = db.Column(db.Boolean, default=False)  # extracted to Event?
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    source = db.relationship("NewsSource", backref="articles")

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "summary": self.summary,
            "published_at": to_iso(self.published_at),
            "fetched_at": to_iso(self.fetched_at),
            "content_hash": self.content_hash,
            "processed": self.processed,
            "created_at": to_iso(self.created_at),
        }

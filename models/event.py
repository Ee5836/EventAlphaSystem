"""Event and event cluster models."""
import uuid
import enum
from datetime import datetime, timezone

from app.extensions import db


class EventStatus(enum.Enum):
    RAW = "raw"
    CLUSTERED = "clustered"
    VERIFIED = "verified"
    SCORED = "scored"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Event(db.Model):
    """Structured event extracted from articles."""
    __tablename__ = "events"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cluster_id = db.Column(db.String(36), db.ForeignKey("event_clusters.id"), nullable=True)
    title = db.Column(db.String(512), nullable=False)
    event_type = db.Column(db.String(64))  # trade_tariff, earnings, merger...
    event_category = db.Column(db.String(64))  # 政策/关税, 财报, 并购...
    entities_json = db.Column(db.JSON, default=list)
    location = db.Column(db.String(256))
    effective_date = db.Column(db.Date, nullable=True)
    affected_industries_json = db.Column(db.JSON, default=list)
    raw_sources_json = db.Column(db.JSON, default=list)
    timeline_json = db.Column(db.JSON, default=list)
    confidence = db.Column(db.Float, default=0.0)  # extraction confidence
    status = db.Column(db.String(32), default=EventStatus.RAW.value)
    uncertainty = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    cluster = db.relationship("EventCluster", backref="events")

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "cluster_id": self.cluster_id,
            "title": self.title,
            "event_type": self.event_type,
            "event_category": self.event_category,
            "entities": self.entities_json,
            "location": self.location,
            "effective_date": to_iso(self.effective_date),
            "affected_industries": self.affected_industries_json,
            "confidence": self.confidence,
            "status": self.status,
            "timeline": self.timeline_json,
            "uncertainty": self.uncertainty,
            "created_at": to_iso(self.created_at),
            "updated_at": to_iso(self.updated_at),
        }


class EventCluster(db.Model):
    """Cluster of related events (deduplicated)."""
    __tablename__ = "event_clusters"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_title = db.Column(db.String(512))
    description = db.Column(db.Text)
    merged_event_ids = db.Column(db.JSON, default=list)
    similarity_score = db.Column(db.Float)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "canonical_title": self.canonical_title,
            "description": self.description,
            "merged_event_ids": self.merged_event_ids,
            "similarity_score": self.similarity_score,
            "created_at": to_iso(self.created_at),
        }

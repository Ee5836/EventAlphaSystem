"""Event card model."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class EventCard(db.Model):
    """Structured event card for display."""
    __tablename__ = "event_cards"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id = db.Column(db.String(36), db.ForeignKey("events.id"), unique=True, nullable=False)
    title = db.Column(db.String(512), nullable=False)
    summary = db.Column(db.Text)
    level = db.Column(db.String(2))  # S / A / B / C
    credibility = db.Column(db.Float)
    credibility_label = db.Column(db.String(32))  # 高可信 / 需确认 / 待验证
    affected_industries = db.Column(db.JSON, default=list)
    event_type = db.Column(db.String(64))
    key_entities = db.Column(db.JSON, default=list)
    timeline_json = db.Column(db.JSON, default=list)
    source_summary = db.Column(db.Text)  # "Reuters + 财联社 共5篇报道"
    risk_flags_json = db.Column(db.JSON, default=list)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    event = db.relationship("Event", backref="card")

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "event_id": self.event_id,
            "title": self.title,
            "summary": self.summary,
            "level": self.level,
            "credibility": self.credibility,
            "credibility_label": self.credibility_label,
            "affected_industries": self.affected_industries,
            "event_type": self.event_type,
            "key_entities": self.key_entities,
            "timeline": self.timeline_json,
            "source_summary": self.source_summary,
            "risk_flags": self.risk_flags_json,
            "created_at": to_iso(self.created_at),
        }

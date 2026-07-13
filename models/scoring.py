"""Event importance scoring model."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class EventScore(db.Model):
    """Five-dimension event importance score."""
    __tablename__ = "event_scores"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id = db.Column(db.String(36), db.ForeignKey("events.id"), unique=True, nullable=False)
    total_score = db.Column(db.Float, nullable=False)  # weighted sum
    market_relevance_score = db.Column(db.Float)  # 30%
    impact_scope_score = db.Column(db.Float)  # 25%
    impact_depth_score = db.Column(db.Float)  # 25%
    interpretability_score = db.Column(db.Float)  # 10%
    timeliness_score = db.Column(db.Float)  # 10%
    level = db.Column(db.String(2))  # S / A / B / C / D
    rationale_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    event = db.relationship("Event", backref="score")

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "event_id": self.event_id,
            "total_score": self.total_score,
            "market_relevance_score": self.market_relevance_score,
            "impact_scope_score": self.impact_scope_score,
            "impact_depth_score": self.impact_depth_score,
            "interpretability_score": self.interpretability_score,
            "timeliness_score": self.timeliness_score,
            "level": self.level,
            "rationale": self.rationale_json,
            "created_at": to_iso(self.created_at),
        }

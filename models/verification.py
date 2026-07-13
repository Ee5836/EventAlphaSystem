"""Credibility verification model."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class VerificationResult(db.Model):
    """Five-dimension credibility verification result."""
    __tablename__ = "verification_results"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id = db.Column(db.String(36), db.ForeignKey("events.id"), unique=True, nullable=False)
    credibility_score = db.Column(db.Float, nullable=False)  # 0.0-1.0
    verification_status = db.Column(db.String(32))  # confirmed / pending / disputed
    source_grade_score = db.Column(db.Float)  # 来源等级 25%
    cross_source_score = db.Column(db.Float)  # 交叉验证 20%
    official_confirm_score = db.Column(db.Float)  # 官方确认 25%
    time_consistency_score = db.Column(db.Float)  # 时间一致性 15%
    historical_accuracy_score = db.Column(db.Float)  # 历史准确率 15%
    evidence_chain_json = db.Column(db.JSON, default=list)
    flags_json = db.Column(db.JSON, default=list)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    event = db.relationship("Event", backref="verification")

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "event_id": self.event_id,
            "credibility_score": self.credibility_score,
            "verification_status": self.verification_status,
            "source_grade_score": self.source_grade_score,
            "cross_source_score": self.cross_source_score,
            "official_confirm_score": self.official_confirm_score,
            "time_consistency_score": self.time_consistency_score,
            "historical_accuracy_score": self.historical_accuracy_score,
            "evidence_chain": self.evidence_chain_json,
            "flags": self.flags_json,
            "created_at": to_iso(self.created_at),
        }

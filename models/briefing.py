"""Daily briefing model."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class DailyBriefing(db.Model):
    """Structured daily investment briefing."""
    __tablename__ = "daily_briefings"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    date = db.Column(db.Date, unique=True, nullable=False)
    title = db.Column(db.String(256))
    executive_summary = db.Column(db.Text)  # 2-3 sentence core conclusion
    market_snapshot_json = db.Column(db.JSON, default=dict)  # indices, sectors, fund flow
    top_events_json = db.Column(db.JSON, default=list)  # top 5-10 events
    event_stats_json = db.Column(db.JSON, default=dict)  # total / S / A / B / C breakdown
    prediction_summary_json = db.Column(db.JSON, default=list)  # key predictions
    sector_heatmap_json = db.Column(db.JSON, default=dict)  # sector performance heatmap
    key_numbers_json = db.Column(db.JSON, default=dict)  # key market numbers
    risk_alert_json = db.Column(db.JSON, default=list)  # upcoming risk events
    full_report_md = db.Column(db.Text)  # complete markdown report
    sources_count = db.Column(db.Integer, default=0)
    articles_processed = db.Column(db.Integer, default=0)
    generated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "date": to_iso(self.date),
            "title": self.title,
            "executive_summary": self.executive_summary,
            "market_snapshot": self.market_snapshot_json,
            "top_events": self.top_events_json,
            "event_stats": self.event_stats_json,
            "prediction_summary": self.prediction_summary_json,
            "sector_heatmap": self.sector_heatmap_json,
            "key_numbers": self.key_numbers_json,
            "risk_alert": self.risk_alert_json,
            "full_report_md": self.full_report_md,
            "sources_count": self.sources_count,
            "articles_processed": self.articles_processed,
            "generated_at": to_iso(self.generated_at),
            "created_at": to_iso(self.created_at),
        }

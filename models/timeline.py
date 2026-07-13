"""Event causal timeline models."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class TimelineNode(db.Model):
    """A node in the event causal timeline graph."""
    __tablename__ = "timeline_nodes"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    node_type = db.Column(db.String(32))  # root_event / derived_event / prediction / market_reaction / verification
    event_id = db.Column(db.String(36), db.ForeignKey("events.id"), nullable=True)
    prediction_id = db.Column(db.String(36), nullable=True)
    title = db.Column(db.String(256))
    description = db.Column(db.Text)
    timestamp = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(32))  # predicted / confirmed / refuted / pending
    confidence = db.Column(db.Float)
    tags_json = db.Column(db.JSON, default=list)
    metadata_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)  # NULL = never expires (backward compat)

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        from datetime import datetime, timezone as tz
        is_expired = False
        if self.expires_at is not None:
            now = datetime.now(tz.utc)
            et = self.expires_at
            if et.tzinfo is None:
                et = et.replace(tzinfo=tz.utc)
            is_expired = et < now
        return {
            "id": self.id,
            "node_type": self.node_type,
            "event_id": self.event_id,
            "prediction_id": self.prediction_id,
            "title": self.title,
            "description": self.description,
            "timestamp": to_iso(self.timestamp),
            "status": self.status,
            "confidence": self.confidence,
            "tags": self.tags_json,
            "metadata": self.metadata_json,
            "expires_at": to_iso(self.expires_at),
            "is_expired": is_expired,
            "created_at": to_iso(self.created_at),
        }

    # Relationships
    event = db.relationship("Event", backref="timeline_nodes")
    outgoing_edges = db.relationship(
        "CausalEdge", foreign_keys="CausalEdge.source_node_id",
        backref="source_node", lazy="dynamic"
    )
    incoming_edges = db.relationship(
        "CausalEdge", foreign_keys="CausalEdge.target_node_id",
        backref="target_node", lazy="dynamic"
    )


class CausalEdge(db.Model):
    """A directed causal link between two timeline nodes."""
    __tablename__ = "causal_edges"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_node_id = db.Column(db.String(36), db.ForeignKey("timeline_nodes.id", ondelete="CASCADE"), nullable=False)
    target_node_id = db.Column(db.String(36), db.ForeignKey("timeline_nodes.id", ondelete="CASCADE"), nullable=False)
    relation_type = db.Column(db.String(32))  # causes / influences / correlates / contradicts
    strength = db.Column(db.Float)  # 0.0-1.0
    logic_chain = db.Column(db.Text)  # reasoning description
    verified = db.Column(db.Boolean, nullable=True, default=None)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_by = db.Column(db.String(64))  # agent / user / llm
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "relation_type": self.relation_type,
            "strength": self.strength,
            "logic_chain": self.logic_chain,
            "verified": self.verified,
            "verified_at": to_iso(self.verified_at),
            "created_by": self.created_by,
            "created_at": to_iso(self.created_at),
        }


class TimelineSnapshot(db.Model):
    """Periodic snapshot of the full timeline graph for comparison."""
    __tablename__ = "timeline_snapshots"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    date = db.Column(db.Date)
    event_count = db.Column(db.Integer, default=0)
    edge_count = db.Column(db.Integer, default=0)
    graph_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "date": to_iso(self.date),
            "event_count": self.event_count,
            "edge_count": self.edge_count,
            "graph": self.graph_json,
            "created_at": to_iso(self.created_at),
        }

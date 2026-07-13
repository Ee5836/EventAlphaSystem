"""Chat session and message models for AI Assistant."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class ChatSession(db.Model):
    """A conversation session with the AI assistant."""
    __tablename__ = "chat_sessions"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(256))  # auto-generated from first message
    summary = db.Column(db.Text)  # compressed conversation summary
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    messages = db.relationship("ChatMessage", backref="session", order_by="ChatMessage.created_at")

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "created_at": to_iso(self.created_at),
            "updated_at": to_iso(self.updated_at),
        }


class ChatMessage(db.Model):
    """A single message in a chat session."""
    __tablename__ = "chat_messages"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = db.Column(db.String(36), db.ForeignKey("chat_sessions.id"), nullable=False)
    role = db.Column(db.String(16), nullable=False)  # user / assistant / tool
    content = db.Column(db.Text)
    reasoning_chain_json = db.Column(db.JSON, default=list)
    tool_calls_json = db.Column(db.JSON, default=list)
    sources_json = db.Column(db.JSON, default=list)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "reasoning_chain": self.reasoning_chain_json,
            "tool_calls": self.tool_calls_json,
            "sources": self.sources_json,
            "created_at": to_iso(self.created_at),
        }


class ResearchNote(db.Model):
    """Saved research note from assistant conversations."""
    __tablename__ = "research_notes"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = db.Column(db.String(36), db.ForeignKey("chat_sessions.id"), nullable=True)
    title = db.Column(db.String(256))
    content = db.Column(db.Text)
    tags_json = db.Column(db.JSON, default=list)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "session_id": self.session_id,
            "title": self.title,
            "content": self.content,
            "tags": self.tags_json,
            "created_at": to_iso(self.created_at),
        }

"""Chat session manager — context window management, summarization."""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.extensions import db
from models.chat import ChatSession, ChatMessage
from assistant.reasoning import ReasoningChain

logger = logging.getLogger(__name__)

# Max messages kept in context window before summarization
MAX_CONTEXT_MESSAGES = 20
# Summarize when we exceed this many
SUMMARY_THRESHOLD = 16


class ChatManager:
    """Manages chat sessions, message history, and context window."""

    def __init__(self):
        pass

    def create_session(self, title: Optional[str] = None) -> ChatSession:
        """Create a new chat session."""
        session = ChatSession(
            title=title or "新对话",
        )
        db.session.add(session)
        db.session.commit()
        return session

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """Get a session by ID."""
        return ChatSession.query.get(session_id)

    def list_sessions(self, offset: int = 0, limit: int = 50) -> tuple[list[ChatSession], int]:
        """List recent sessions with pagination. Returns (sessions, total_count)."""
        total = ChatSession.query.count()
        sessions = (
            ChatSession.query
            .order_by(ChatSession.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return sessions, total

    def delete_session(self, session_id: str) -> bool:
        """Delete a session, all its messages, and associated research notes."""
        session = self.get_session(session_id)
        if not session:
            return False
        from models.chat import ResearchNote
        ResearchNote.query.filter_by(session_id=session_id).delete()
        ChatMessage.query.filter_by(session_id=session_id).delete()
        db.session.delete(session)
        db.session.commit()
        return True

    def delete_all_sessions(self) -> int:
        """Delete all chat sessions, messages, and notes. Returns count of deleted sessions."""
        from models.chat import ResearchNote, ChatMessage
        count = ChatSession.query.count()
        if count == 0:
            return 0
        ResearchNote.query.filter(ResearchNote.session_id.isnot(None)).delete()
        ChatMessage.query.delete()
        ChatSession.query.delete()
        db.session.commit()
        return count

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        reasoning_chain: Optional[ReasoningChain] = None,
        tool_calls: Optional[list[dict]] = None,
        sources: Optional[list[dict]] = None,
    ) -> ChatMessage:
        """Add a message to a session."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            reasoning_chain_json=reasoning_chain.to_dict() if reasoning_chain else [],
            tool_calls_json=tool_calls or [],
            sources_json=sources or [],
        )
        db.session.add(message)

        # Auto-title from first user message
        if role == "user" and session.title == "新对话":
            session.title = content[:50] + ("..." if len(content) > 50 else "")

        session.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return message

    def get_context_messages(self, session_id: str) -> list[dict]:
        """Get recent messages for the LLM context window.

        If the conversation exceeds SUMMARY_THRESHOLD, older messages
        are compressed into a summary.
        """
        session = self.get_session(session_id)
        if not session:
            return []

        messages = (
            ChatMessage.query
            .filter_by(session_id=session_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )

        if len(messages) <= MAX_CONTEXT_MESSAGES:
            return [
                {"role": m.role, "content": m.content}
                for m in messages
            ]

        # Keep last N messages, summarize older ones
        recent = messages[-MAX_CONTEXT_MESSAGES:]
        older = messages[:-MAX_CONTEXT_MESSAGES]

        # Build summary of older messages
        summary_parts = []
        for m in older:
            summary_parts.append(f"[{m.role}]: {m.content[:200]}")

        summary = "以下为对话历史摘要：\n" + "\n".join(summary_parts[-10:])

        context = [{"role": "system", "content": summary}]
        context.extend({"role": m.role, "content": m.content} for m in recent)
        return context

    def get_full_history(self, session_id: str) -> list[dict]:
        """Get all messages for a session (for display)."""
        messages = (
            ChatMessage.query
            .filter_by(session_id=session_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "reasoning_chain": m.reasoning_chain_json,
                "tool_calls": m.tool_calls_json,
                "sources": m.sources_json,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]

    def rename_session(self, session_id: str, title: str) -> bool:
        """Rename a session."""
        session = self.get_session(session_id)
        if not session:
            return False
        session.title = title
        db.session.commit()
        return True

    def delete_message(self, session_id: str, message_id: str) -> dict:
        """Delete a single message. If it's a user message, also delete the
        adjacent assistant response that follows it (they come in pairs).

        Returns dict with {deleted_ids: [str]} for frontend DOM removal.
        """
        message = ChatMessage.query.filter_by(
            id=message_id, session_id=session_id
        ).first()
        if not message:
            return {"deleted_ids": [], "error": "Message not found"}

        deleted_ids = []

        if message.role == "user":
            # Find the assistant response immediately following this user message
            next_msg = (
                ChatMessage.query
                .filter_by(session_id=session_id)
                .filter(ChatMessage.created_at > message.created_at)
                .order_by(ChatMessage.created_at.asc())
                .first()
            )
            if next_msg and next_msg.role == "assistant":
                db.session.delete(next_msg)
                deleted_ids.append(next_msg.id)

        deleted_ids.insert(0, message.id)
        db.session.delete(message)

        # Touch session timestamp
        session = self.get_session(session_id)
        if session:
            session.updated_at = datetime.now(timezone.utc)

        db.session.commit()
        return {"deleted_ids": deleted_ids}

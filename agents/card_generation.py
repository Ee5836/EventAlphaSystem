"""Card Generation Agent — generates human-readable event cards."""
import logging

from app.extensions import db
from agents.base import BaseAgent, AgentResult

logger = logging.getLogger("agent.card_generation")

CARD_SYSTEM_PROMPT = """You are a financial event analyst. Generate a structured event card in Chinese.

Output JSON:
{
  "title": "Event title (max 100 chars)",
  "summary": "1-2 paragraph summary explaining what happened and why it matters for investors",
  "credibility_label": "高可信" / "需确认" / "待验证",
  "key_entities": ["entity1", "entity2"],
  "source_summary": "e.g., Reuters + 财联社 + 华尔街见闻 共5篇报道",
  "risk_flags": ["flag1"] or [],
  "affected_industries": ["industry1", "industry2"]
}"""


class CardGenerationAgent(BaseAgent):
    """Generates structured event cards from scored events."""
    name = "card_generation"

    def run(self, events: list = None, min_level: str = "B", **kwargs) -> AgentResult:
        """Generate cards for scored events concurrently.

        Args:
            events: List of Event objects with status='scored'. If None, queries DB.
            min_level: Minimum event level to generate cards for. Default 'B'.

        Returns:
            AgentResult with output = {"cards": [EventCard]}
        """
        from models.event import Event, EventStatus
        from models.card import EventCard
        from models.scoring import EventScore
        from utils.concurrent import run_concurrently

        if events is None:
            events = Event.query.filter_by(status=EventStatus.SCORED.value).all()

        # Filter by minimum level and serialize to dicts
        level_order = {"S": 0, "A": 1, "B": 2, "C": 3}
        min_rank = level_order.get(min_level, 2)

        items = []
        for event in events:
            score = EventScore.query.filter_by(event_id=event.id).first()
            if not score:
                continue
            if level_order.get(score.level, 3) > min_rank:
                continue
            # Skip if card already exists
            existing = EventCard.query.filter_by(event_id=event.id).first()
            if existing:
                continue
            items.append({
                "id": event.id,
                "title": event.title or "",
                "event_type": event.event_type or "other",
                "event_category": event.event_category or "其他",
                "entities": event.entities_json or [],
                "industries": event.affected_industries_json or [],
                "level": score.level or "B",
                "confidence": event.confidence or 0.5,
                "source_count": len(event.raw_sources_json or []),
            })

        if not items:
            return AgentResult(
                success=True,
                output={"cards": []},
                metadata={"message": f"No events with level >= {min_level} to generate cards for"},
            )

        llm = self._get_llm()
        max_workers = self.config.get("LLM_MAX_CONCURRENCY", 5)

        def _generate_card(item: dict):  # -> Optional[dict]
            """Worker: generate one card in its own thread/DB session."""
            from models.event import Event, EventStatus
            from models.card import EventCard

            try:
                user_msg = (
                    f"Event: {item['title']}\n"
                    f"Type: {item['event_type']} ({item['event_category']})\n"
                    f"Entities: {', '.join(item.get('entities', []) or [])}\n"
                    f"Industries: {', '.join(item.get('industries', []) or [])}\n"
                    f"Level: {item.get('level', 'B')}\n"
                    f"Credibility Score: {item.get('confidence', 0.5):.2f}\n"
                    f"Sources: {item.get('source_count', 0)} articles"
                )

                result = llm.complete_json(CARD_SYSTEM_PROMPT, user_msg, temperature=0.3)

                card = EventCard(
                    event_id=item["id"],
                    title=result.get("title", item["title"]),
                    summary=result.get("summary", ""),
                    level=item.get("level", "B"),
                    credibility=item.get("confidence", 0.5),
                    credibility_label=result.get("credibility_label", "需确认"),
                    affected_industries=result.get("affected_industries", item.get("industries", []) or []),
                    event_type=item["event_type"],
                    key_entities=result.get("key_entities", item.get("entities", []) or []),
                    timeline_json=[],
                    source_summary=result.get("source_summary", ""),
                    risk_flags_json=result.get("risk_flags", []),
                )
                db.session.add(card)

                # Update event status to published
                event = Event.query.filter_by(id=item["id"]).first()
                if event:
                    event.status = EventStatus.PUBLISHED.value

                db.session.commit()

                logger.info(f"Card: generated for '{item['title'][:50]}'")
                return {"event_id": item["id"], "title": card.title}
            except Exception as e:
                logger.error(f"Card generation failed for '{item.get('title', '')[:50]}': {e}")
                try:
                    db.session.rollback()
                except Exception:
                    pass
                return None

        successes, failures = run_concurrently(
            items=items,
            worker_fn=_generate_card,
            max_workers=max_workers,
            description="card_generation",
        )

        logger.info(
            f"Card generation complete: {len(successes)} cards, {len(failures)} failed"
        )

        return AgentResult(
            success=len(successes) > 0,
            output={"cards": successes, "failures": failures},
            metadata={
                "total_cards": len(successes),
                "total_failed": len(failures),
                "max_workers": max_workers,
            },
        )

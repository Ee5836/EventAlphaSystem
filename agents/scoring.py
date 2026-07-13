"""Event Scoring Agent — rates event importance using LLM + statistical decay."""
import logging
from datetime import datetime, timezone

from app.extensions import db
from agents.base import BaseAgent, AgentResult
from agents.stats_utils import event_timeliness, LEVEL_WEIGHT

logger = logging.getLogger("agent.scoring")

SCORING_SYSTEM_PROMPT = """You are a financial event importance evaluator.
Assess the given event's potential impact on financial markets.

Rate each dimension from 0.0 (no impact) to 1.0 (maximum impact):

1. market_relevance (30%): How directly does this affect capital markets?
2. impact_scope (25%): How many industries/asset classes are affected?
3. impact_depth (25%): How severe is the impact on affected industries?
4. interpretability (10%): How clear is the cause-effect chain?
5. timeliness (10%): (will be auto-adjusted — rate raw content freshness here)

Also assign a level: S (>=0.85), A (0.70-0.84), B (0.50-0.69), C (<0.50)

Output JSON:
{
  "market_relevance": 0.0-1.0,
  "impact_scope": 0.0-1.0,
  "impact_depth": 0.0-1.0,
  "interpretability": 0.0-1.0,
  "timeliness": 0.0-1.0,
  "level": "S/A/B/C",
  "rationale": "Brief explanation (1-2 sentences in Chinese)"
}"""


class ScoringAgent(BaseAgent):
    """Rates event importance on five dimensions using LLM."""
    name = "scoring"

    WEIGHTS = {
        "market_relevance": 0.30,
        "impact_scope": 0.25,
        "impact_depth": 0.25,
        "interpretability": 0.10,
        "timeliness": 0.10,
    }

    def run(self, events: list = None, **kwargs) -> AgentResult:
        """Score verified events concurrently via ThreadPoolExecutor.

        Args:
            events: List of Event objects with status='verified'. If None, queries DB.

        Returns:
            AgentResult with output = {"scores": [EventScore]}
        """
        from models.event import Event, EventStatus
        from models.scoring import EventScore
        from utils.concurrent import run_concurrently

        if events is None:
            events = Event.query.filter_by(status=EventStatus.VERIFIED.value).all()

        # Filter out already-scored events and serialize to dicts
        items = []
        for event in events:
            existing = EventScore.query.filter_by(event_id=event.id).first()
            if existing:
                continue
            items.append({
                "id": event.id,
                "title": event.title or "",
                "event_type": event.event_type or "other",
                "event_category": event.event_category or "其他",
                "entities": event.entities_json or [],
                "industries": event.affected_industries_json or [],
                "location": event.location or "Unknown",
            })

        if not items:
            return AgentResult(
                success=True,
                output={"scores": []},
                metadata={"message": "No events to score (all already scored)"},
            )

        llm = self._get_llm()
        max_workers = self.config.get("LLM_MAX_CONCURRENCY", 5)

        # Build worker closure capturing llm + weights
        weights = self.WEIGHTS

        def _score_one(item: dict):  # -> Optional[dict]
            """Worker: score one event in its own thread/DB session."""
            from models.event import Event, EventStatus
            from models.scoring import EventScore

            user_msg = (
                f"Event: {item['title']}\n"
                f"Type: {item['event_type']}\n"
                f"Category: {item['event_category']}\n"
                f"Entities: {', '.join(item.get('entities', []) or [])}\n"
                f"Industries: {', '.join(item.get('industries', []) or [])}\n"
                f"Location: {item.get('location', 'Unknown')}"
            )

            result = llm.complete_json(SCORING_SYSTEM_PROMPT, user_msg, temperature=0.1)

            # ── Statistical adjustment: blend LLM timeliness with event age decay ──
            llm_timeliness = result.get("timeliness", 0.5)
            event = Event.query.filter_by(id=item["id"]).first()
            if event and event.created_at:
                age_decay = event_timeliness(event.created_at)
                # Blend: 40% LLM assessment + 60% mathematical age decay
                adjusted_timeliness = round(0.4 * llm_timeliness + 0.6 * age_decay, 4)
            else:
                adjusted_timeliness = llm_timeliness

            # ── Level adjustment: apply Wilson-like conservative bound ──
            # When LLM assigns S but the event is old or has few entities, be conservative
            llm_level = result.get("level", "B")
            industry_count = len(item.get("industries", []) or [])
            entity_count = len(item.get("entities", []) or [])
            # More industries/entities → more confidence in high level
            scope_factor = min(1.0, (industry_count + entity_count) / 6.0)  # 6+ total = full confidence
            if llm_level == "S" and scope_factor < 0.5:
                llm_level = "A"  # demote S→A when scope evidence is thin

            total = sum(
                result.get(k, 0.5) * w
                for k, w in weights.items()
            )
            # Replace the timeliness component with adjusted value
            total -= result.get("timeliness", 0.5) * weights.get("timeliness", 0.10)
            total += adjusted_timeliness * weights.get("timeliness", 0.10)
            total = round(total, 4)

            # ── Wilson-based confidence adjustment ──
            # If the event has very few associated entities/industries,
            # scale total_score down slightly (low evidence → lower confidence)
            evidence_factor = 0.9 + 0.1 * scope_factor  # 0.9–1.0 range
            total = round(total * evidence_factor, 4)

            es = EventScore(
                event_id=item["id"],
                total_score=total,
                market_relevance_score=result.get("market_relevance", 0.5),
                impact_scope_score=result.get("impact_scope", 0.5),
                impact_depth_score=result.get("impact_depth", 0.5),
                interpretability_score=result.get("interpretability", 0.5),
                timeliness_score=adjusted_timeliness,
                level=llm_level,
                rationale_json={"rationale": result.get("rationale", ""),
                                "timeliness_raw": llm_timeliness,
                                "timeliness_adjusted": adjusted_timeliness},
            )
            db.session.add(es)

            # Update event status to scored
            if event:
                event.status = EventStatus.SCORED.value

            db.session.commit()

            logger.info(f"Scoring: '{item['title'][:50]}' → level={es.level} total={total:.2f} "
                        f"(timeliness: {llm_timeliness:.2f}→{adjusted_timeliness:.2f})")
            return {"event_id": item["id"], "level": es.level, "total": total}

        successes, failures = run_concurrently(
            items=items,
            worker_fn=_score_one,
            max_workers=max_workers,
            description="scoring",
        )

        logger.info(
            f"Scoring complete: {len(successes)} scored, {len(failures)} failed"
        )

        return AgentResult(
            success=len(successes) > 0,
            output={"scores": successes, "failures": failures},
            metadata={
                "total_scored": len(successes),
                "total_failed": len(failures),
                "max_workers": max_workers,
            },
        )

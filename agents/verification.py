"""Credibility Verification Agent — five-dimension credibility scoring."""
import logging

from app.extensions import db
from agents.base import BaseAgent, AgentResult

logger = logging.getLogger("agent.verification")


class VerificationAgent(BaseAgent):
    """Five-dimension credibility verification for events."""
    name = "verification"

    # Weights
    W_SOURCE_GRADE = 0.25
    W_CROSS_SOURCE = 0.20
    W_OFFICIAL_CONFIRM = 0.25
    W_TIME_CONSISTENCY = 0.15
    W_HISTORICAL_ACCURACY = 0.15

    def run(self, events: list = None, **kwargs) -> AgentResult:
        """Run credibility verification on clustered events.

        Args:
            events: List of Event objects with status='clustered'. If None, queries DB.

        Returns:
            AgentResult with output = {"verifications": [VerificationResult]}
        """
        from models.event import Event, EventStatus
        from models.verification import VerificationResult

        if events is None:
            events = Event.query.filter_by(status=EventStatus.CLUSTERED.value).all()

        verifications = []
        for event in events:
            # Skip already verified
            existing = VerificationResult.query.filter_by(event_id=event.id).first()
            if existing:
                verifications.append(existing)
                continue

            # Compute scores
            source_grade = self._calc_source_grade(event)
            cross_source = self._calc_cross_source(event)
            official_confirm = self._calc_official_confirm(event)
            time_consistency = self._calc_time_consistency(event)
            historical_accuracy = self._calc_historical_accuracy(event)

            total = (
                source_grade * self.W_SOURCE_GRADE
                + cross_source * self.W_CROSS_SOURCE
                + official_confirm * self.W_OFFICIAL_CONFIRM
                + time_consistency * self.W_TIME_CONSISTENCY
                + historical_accuracy * self.W_HISTORICAL_ACCURACY
            )

            # Determine status
            if total >= 0.85:
                status = "confirmed"
            elif total >= 0.50:
                status = "pending"
            else:
                status = "disputed"

            flags = []
            if cross_source < 0.5:
                flags.append("single_source")
            if official_confirm < 0.3:
                flags.append("unconfirmed_claim")

            vr = VerificationResult(
                event_id=event.id,
                credibility_score=round(total, 4),
                verification_status=status,
                source_grade_score=round(source_grade, 4),
                cross_source_score=round(cross_source, 4),
                official_confirm_score=round(official_confirm, 4),
                time_consistency_score=round(time_consistency, 4),
                historical_accuracy_score=round(historical_accuracy, 4),
                evidence_chain_json=event.raw_sources_json or [],
                flags_json=flags,
            )
            db.session.add(vr)
            verifications.append(vr)

            # Update event status
            event.status = EventStatus.VERIFIED.value
            logger.info(f"Verification: event '{event.title[:50]}' score={total:.2f} status={status}")

        db.session.commit()
        return AgentResult(
            success=True,
            output={"verifications": verifications},
            metadata={
                "total_verified": len(verifications),
                "avg_score": round(
                    sum(v.credibility_score for v in verifications) / max(len(verifications), 1), 4
                ),
            },
        )

    def _calc_source_grade(self, event) -> float:
        """Calculate source authority grade."""
        from models.source import NewsSource
        sources = event.raw_sources_json or []
        if not sources:
            return 0.3

        scores = []
        for s in sources:
            source_id = s.get("source_id")
            # Fallback: extraction results may use "article_id" instead
            if source_id is None:
                article_id = s.get("article_id")
                if article_id:
                    from models.source import RawArticle
                    article = RawArticle.query.get(article_id)
                    if article:
                        source_id = article.source_id
            src = NewsSource.query.filter_by(id=source_id).first() if source_id else None
            scores.append(src.credibility if src else 0.5)
        return sum(scores) / len(scores)

    def _calc_cross_source(self, event) -> float:
        """Score based on number of distinct sources."""
        sources = event.raw_sources_json or []
        distinct = len(set(s.get("source_id", "") for s in sources))
        if distinct >= 3:
            return 0.9
        elif distinct >= 2:
            return 0.6
        else:
            return 0.3

    def _calc_official_confirm(self, event) -> float:
        """Check if official sources confirm the event.

        Uses simple keyword matching for MVP. Phase 2: LLM-based verification.
        """
        official_keywords = [
            "官方", "公告", "宣布", "声明", "公布", "披露",
            "official", "announced", "statement", "confirmed",
            "证监会", "央行", "国务院", "商务部", "财政部",
            "SEC", "Fed", "White House", "European Commission",
        ]
        title = event.title.lower() if event.title else ""
        entities = " ".join(event.entities_json or []).lower()

        count = sum(1 for kw in official_keywords if kw.lower() in title or kw.lower() in entities)
        if count >= 3:
            return 0.9
        elif count >= 1:
            return 0.6
        return 0.2

    def _calc_time_consistency(self, event) -> float:
        """Check temporal consistency across sources."""
        sources = event.raw_sources_json or []
        if len(sources) <= 1:
            return 0.5
        return 0.8  # Default: assume consistent for MVP

    def _calc_historical_accuracy(self, event) -> float:
        """Source historical accuracy (static for MVP, dynamic in Phase 2)."""
        return 0.6  # Default baseline

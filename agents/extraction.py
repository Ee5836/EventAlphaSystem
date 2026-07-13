"""Event Extraction Agent — batch LLM extraction from articles (concurrent)."""
import json
import logging
from datetime import datetime, timezone

from app.extensions import db
from agents.base import BaseAgent, AgentResult

logger = logging.getLogger("agent.extraction")

EXTRACTION_SYSTEM_PROMPT = """You are an expert financial event extraction system.
Extract structured event information from each news article provided.

For EACH article, output a JSON object:
{
  "title": "Concise event title (max 100 chars)",
  "event_type": "trade_tariff|monetary_policy|fiscal_policy|corporate_earnings|merger_acquisition|ipo|regulatory_change|geopolitical|natural_disaster|industry_news|technology_breakthrough|commodity_price|currency_movement|other",
  "event_category": "政策/关税|货币政策|财报|并购|监管|地缘政治|自然灾害|行业动态|技术突破|大宗商品|汇率|其他",
  "entities": ["entity1"],
  "location": "geographic location",
  "effective_date": "YYYY-MM-DD or null",
  "affected_industries": ["industry1"],
  "confidence": 0.0-1.0,
  "is_investment_relevant": true/false
}

IMPORTANT: Return a JSON ARRAY, one element per article. Keep titles <100 chars."""

BATCH_SIZE = 5  # articles per LLM call (smaller = faster response per call)
MAX_ARTICLES = 100


class ExtractionAgent(BaseAgent):
    """Extracts structured events from articles using concurrent LLM batch calls."""
    name = "extraction"

    def run(self, articles: list = None, **kwargs) -> AgentResult:
        """Extract events from articles concurrently.

        Args:
            articles: List of RawArticle objects. If None, queries DB.

        Returns:
            AgentResult with output = {"events": [Event], "failures": [...]}
        """
        from models.source import RawArticle
        from utils.concurrent import run_concurrently

        if articles is None:
            articles = RawArticle.query.filter_by(processed=False) \
                .order_by(RawArticle.created_at.desc()) \
                .limit(MAX_ARTICLES).all()

        if not articles:
            return AgentResult(
                success=True,
                output={"events": [], "failures": []},
                metadata={"message": "No unprocessed articles found"},
            )

        # Split into batches and serialize each batch
        batches = [articles[i:i + BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]

        batch_items = []
        for batch in batches:
            batch_items.append({
                "articles": [
                    {
                        "id": a.id,
                        "text": ((a.title or "")[:200] + "\n" + (a.content or a.summary or "")[:400]),
                    }
                    for a in batch
                ],
            })

        llm = self._get_llm()
        max_workers = self.config.get("LLM_MAX_CONCURRENCY", 5)

        def _process_batch(batch_data: dict):  # -> Optional[dict]
            """Worker: extract events from one batch of articles.

            Returns raw dict (no SQLAlchemy objects) for main-thread reconciliation.
            """
            articles_data = batch_data["articles"]

            parts = []
            for i, a in enumerate(articles_data):
                parts.append(f"[{i + 1}] {a['text']}")
            articles_text = "\n\n".join(parts)

            user_message = (
                f"Extract events from these {len(articles_data)} articles. "
                f"Return a JSON array:\n\n{articles_text}"
            )

            try:
                results = llm.complete_json(
                    EXTRACTION_SYSTEM_PROMPT,
                    user_message,
                    temperature=0.1,
                    max_tokens=3000,
                )

                if not isinstance(results, list):
                    results = [results]

                extracted = []
                failures = []
                for i, result in enumerate(results):
                    if i >= len(articles_data):
                        break

                    if not isinstance(result, dict):
                        continue

                    article_id = articles_data[i]["id"]

                    if not result.get("is_investment_relevant", True):
                        extracted.append({"article_id": article_id, "skipped": True})
                        continue

                    try:
                        effective_date = result.get("effective_date")
                        if effective_date:
                            try:
                                from datetime import date
                                date.fromisoformat(effective_date)
                            except (ValueError, TypeError):
                                effective_date = None

                        extracted.append({
                            "article_id": article_id,
                            "skipped": False,
                            "title": result.get("title", "Untitled Event")[:100],
                            "event_type": result.get("event_type", "other"),
                            "event_category": result.get("event_category", "其他"),
                            "entities": result.get("entities", []),
                            "location": result.get("location", ""),
                            "effective_date": effective_date,
                            "affected_industries": result.get("affected_industries", []),
                            "confidence": result.get("confidence", 0.5),
                        })
                    except Exception as e:
                        logger.error(f"Extraction item {i} failed: {e}")
                        failures.append({"article_id": article_id, "error": str(e)})

                return {
                    "extracted": extracted,
                    "failures": failures,
                }
            except Exception as e:
                logger.error(f"Batch extraction LLM failed: {e}")
                return {
                    "extracted": [],
                    "failures": [{"article_id": a["id"], "error": str(e)} for a in articles_data],
                }

        successes, failures = run_concurrently(
            items=batch_items,
            worker_fn=_process_batch,
            max_workers=max_workers,
            description="extraction",
        )

        # ── Main-thread reconciliation: create Event objects ──────────
        all_events = []
        all_failures = []
        processed_ids = set()

        for batch_result in successes:
            if not batch_result:
                continue
            for ext in batch_result.get("extracted", []):
                article_id = ext["article_id"]
                processed_ids.add(article_id)
                if ext.get("skipped"):
                    continue

                try:
                    event = self._create_event(ext)
                    all_events.append(event)
                except Exception as e:
                    logger.error(f"Event creation failed for article {article_id}: {e}")
                    all_failures.append({"article_id": article_id, "error": str(e)})

            for f in batch_result.get("failures", []):
                all_failures.append(f)
                processed_ids.add(f["article_id"])

        # Process failures from run_concurrently
        for f in failures:
            item = f.get("item", {})
            for a in item.get("articles", []):
                all_failures.append({"article_id": a["id"], "error": f.get("error", "unknown")})

        # Mark successfully processed articles only — failed ones remain for retry
        failed_ids = {f["article_id"] for f in all_failures if f.get("article_id")}
        all_article_ids = {a.id for a in articles}
        from models.source import RawArticle
        for article_id in all_article_ids:
            if article_id not in failed_ids:
                RawArticle.query.filter_by(id=article_id).update({"processed": True})

        db.session.commit()

        logger.info(
            f"Extraction complete: {len(all_events)} events, "
            f"{len(all_failures)} failures from {len(articles)} articles"
        )

        return AgentResult(
            success=len(all_events) > 0,
            output={"events": all_events, "failures": all_failures},
            metadata={
                "total_processed": len(articles),
                "events_extracted": len(all_events),
                "failures": len(all_failures),
                "batches": len(batches),
                "max_workers": max_workers,
            },
        )

    def _create_event(self, ext: dict):
        """Create an Event model from extraction result dict."""
        from models.event import Event

        effective_date = None
        if ext.get("effective_date"):
            try:
                from datetime import date
                effective_date = date.fromisoformat(ext["effective_date"])
            except (ValueError, TypeError):
                pass

        # Build minimal source info from article_id reference
        event = Event(
            title=ext.get("title", "Untitled Event"),
            event_type=ext.get("event_type", "other"),
            event_category=ext.get("event_category", "其他"),
            entities_json=ext.get("entities", []),
            location=ext.get("location", ""),
            effective_date=effective_date,
            affected_industries_json=ext.get("affected_industries", []),
            raw_sources_json=[{"article_id": ext["article_id"]}],
            confidence=ext.get("confidence", 0.5),
            status="raw",
        )
        db.session.add(event)
        db.session.flush()
        return event

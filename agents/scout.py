"""Information Scout Agent — collects articles from all active sources."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from app.extensions import db
from agents.base import BaseAgent, AgentResult
from sources.registry import get_connector, list_connectors

logger = logging.getLogger("agent.scout")


class ScoutAgent(BaseAgent):
    """Collects raw articles from all active news sources."""
    name = "scout"

    def run(self, source_names: list[str] = None, force: bool = False,
            **kwargs) -> AgentResult:
        """Fetch articles from all active sources.

        Args:
            source_names: Optional list of source names to fetch.
                         If None, fetches from all configured sources.
            force: If True, skip poll_interval check (fetch all sources regardless).

        Returns:
            AgentResult with output = {"articles": [RawArticle], "source_counts": dict}
        """
        from models.source import NewsSource, RawArticle
        from utils.concurrent import run_concurrently

        if source_names is None:
            source_names = self._get_active_sources()

        # ── Pre-resolve source DB IDs (avoid race in concurrent threads) ──
        source_id_map = {}
        for name in source_names:
            source_id_map[name] = self._get_source_id(name)

        # ── Filter by poll_interval ───────────────────────────────────
        now = datetime.now(timezone.utc)
        sources_to_fetch = []
        skipped_sources = []

        for name in source_names:
            source_rec = NewsSource.query.filter_by(name=name).first()
            if not force and source_rec and source_rec.last_fetch_at:
                last_fetch = source_rec.last_fetch_at
                if last_fetch.tzinfo is None:
                    last_fetch = last_fetch.replace(tzinfo=timezone.utc)
                elapsed = (now - last_fetch).total_seconds()
                interval = source_rec.poll_interval or 3600
                if elapsed < interval:
                    remaining = int(interval - elapsed)
                    skipped_sources.append((name, remaining))
                    continue
            sources_to_fetch.append(name)

        if skipped_sources:
            skip_info = ", ".join(
                f"{n}({r}s left)" for n, r in skipped_sources
            )
            logger.info(f"Scout: skipped {len(skipped_sources)} sources within "
                        f"poll_interval: {skip_info}")

        if not sources_to_fetch:
            logger.info("Scout: no sources to fetch (all within poll_interval)")
            return AgentResult(
                success=True,
                output={"articles": [], "source_counts": {}},
                errors=[],
                metadata={
                    "total_articles": 0,
                    "skipped_sources": len(skipped_sources),
                    "fetched_sources": 0,
                },
            )

        # ── Parallel fetch ────────────────────────────────────────────
        # Limit concurrency to avoid overloading sources
        max_concurrency = min(
            len(sources_to_fetch),
            max(2, self.config.get("LLM_MAX_CONCURRENCY", 5)),
        )

        fetch_items = [{"name": n, "source_id": source_id_map[n]}
                       for n in sources_to_fetch]

        def _fetch_one(item: dict):  # -> dict or None
            """Worker: fetch from a single source (runs in thread pool)."""
            name = item["name"]
            source_id = item["source_id"]
            connector = self._resolve_connector(name)

            if connector is None:
                logger.warning(f"No connector available for source: {name}")
                return {"name": name, "count": 0, "articles": [],
                        "error": f"No connector for {name}"}

            try:
                raw_articles = connector.fetch()
                saved_count = 0
                for article_data in raw_articles:
                    # Dedup by URL
                    existing = RawArticle.query.filter_by(
                        url=article_data["url"]
                    ).first()
                    if existing:
                        continue

                    article = RawArticle(
                        source_id=source_id,
                        url=article_data["url"],
                        title=article_data.get("title", ""),
                        content=article_data.get("content", ""),
                        summary=article_data.get("summary", ""),
                        published_at=article_data.get("published_at"),
                        content_hash=article_data.get("content_hash", hashlib.sha256(
                            article_data["url"].encode()
                        ).hexdigest()),
                        raw_metadata=article_data.get("metadata", {}),
                    )
                    db.session.add(article)
                    saved_count += 1

                db.session.commit()

                # Query back saved articles (use source_id for thread-safe query)
                saved_articles = RawArticle.query.filter(
                    RawArticle.source_id == source_id
                ).order_by(RawArticle.created_at.desc()).limit(
                    max(saved_count, 1)
                ).all()

                logger.info(f"Scout: {name} fetched {saved_count} new articles")

                # Update source last_fetch status
                self._update_source_status(name, "success", saved_count)

                return {
                    "name": name,
                    "count": saved_count,
                    "articles": saved_articles,
                    "error": None,
                }

            except Exception as e:
                error_msg = f"Source '{name}' fetch failed: {e}"
                logger.error(error_msg)
                self._update_source_status(name, "error", 0)
                return {"name": name, "count": 0, "articles": [],
                        "error": error_msg}

        successes, failures = run_concurrently(
            items=fetch_items,
            worker_fn=_fetch_one,
            max_workers=max_concurrency,
            description="scout_fetch",
        )

        # ── Aggregate results ─────────────────────────────────────────
        all_articles = []
        source_counts = {}
        errors = []

        for result in successes:
            source_counts[result["name"]] = result["count"]
            all_articles.extend(result["articles"])
            if result.get("error"):
                errors.append(result["error"])

        for failure in failures:
            name = failure.get("item", {}).get("name", "unknown")
            errors.append(failure.get("error", f"Unknown error for {name}"))

        logger.info(
            f"Scout complete: {len(sources_to_fetch)} sources → "
            f"{sum(source_counts.values())} new articles "
            f"({len(skipped_sources)} skipped, {len(errors)} errors)"
        )

        return AgentResult(
            success=len(errors) == 0,
            output={"articles": all_articles, "source_counts": source_counts},
            errors=errors,
            metadata={
                "total_articles": len(all_articles),
                "skipped_sources": len(skipped_sources),
                "fetched_sources": len(sources_to_fetch),
                "max_concurrency": max_concurrency,
            },
        )

    # ── Source resolution ─────────────────────────────────────────────
    def _resolve_connector(self, name: str):
        """Resolve a connector for the given source name.

        Priority:
        1. Registered connector (cls / wallstcn / reuters / ak_cctv / ak_futures)
        2. GenericConnector for user-added sources (api / webpage / rss)
        """
        from models.source import NewsSource
        source_rec = NewsSource.query.filter_by(name=name).first()

        # Try registered connector first
        connector = get_connector(name, self.config)
        if connector is not None:
            # Pass source_record if the connector supports it (e.g. AkshareConnector)
            if hasattr(connector, 'source_record') and source_rec is not None:
                connector.source_record = source_rec
            return connector

        # Fall back to GenericConnector for user-added sources
        if source_rec is not None:
            from sources.generic import GenericConnector
            return GenericConnector(source_record=source_rec, config=self.config)

        return None

    # ── Active source enumeration ─────────────────────────────────────
    def _get_active_sources(self) -> list[str]:
        """Get list of active source names, respecting DB is_active flags.

        - System sources (cls/wallstcn/reuters): only included if is_active=True in DB
        - User sources: included if is_active=True in DB
        """
        from models.source import NewsSource

        try:
            # Query ALL active sources from DB — respects both system and user toggles
            active_sources = NewsSource.query.filter_by(is_active=True).all()
            names = [s.name for s in active_sources]
        except Exception:
            # Fall back to config / registry (best-effort)
            config_sources = self.config.get("NEWS_SOURCES", "")
            if config_sources:
                names = [s.strip() for s in config_sources.split(",") if s.strip()]
            else:
                names = list_connectors()

        return names

    # ── DB helpers ────────────────────────────────────────────────────
    def _get_source_id(self, name: str) -> str:
        """Get or create source record ID."""
        from models.source import NewsSource

        source = NewsSource.query.filter_by(name=name).first()
        if source:
            return source.id

        # Auto-create if not exists
        display_names = {
            "cls": "新浪财经",
            "wallstcn": "华尔街见闻",
            "reuters": "国际财经",
        }
        source = NewsSource(
            name=name,
            display_name=display_names.get(name, name),
            source_type="rss",
            is_system=True,
            created_by="system",
        )
        db.session.add(source)
        db.session.commit()
        return source.id

    def _update_source_status(self, name: str, status: str, count: int):
        """Update last_fetch info on source record."""
        from models.source import NewsSource
        try:
            source = NewsSource.query.filter_by(name=name).first()
            if source:
                source.last_fetch_at = datetime.now(timezone.utc)
                source.last_fetch_status = status
                source.last_fetch_count = count
                db.session.commit()
        except Exception:
            pass

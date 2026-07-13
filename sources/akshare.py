"""AKShare-based news connectors — 东方财富/央视/上期所等."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sources.base import AbstractSourceConnector
from sources.registry import register_connector

logger = logging.getLogger("source.akshare")

# ── AKShare function mapping ───────────────────────────────────────────
# Each entry maps a source name to (ak_function, article_key_map)
# ak_function: callable with no args → DataFrame
# article_key_map: {df_column → article_field}
FUNCTION_MAP = {
    "ak_cctv": {
        "func_name": "news_cctv",
        "col_map": {"title": "title", "content": "content", "date": "published_at"},
    },
    "ak_futures": {
        "func_name": "futures_news_shmet",
        "col_map": {"内容": "content", "发布时间": "published_at"},
    },
}


@register_connector("ak_cctv")
@register_connector("ak_futures")
class AkshareConnector(AbstractSourceConnector):
    """Generic AKShare data connector.

    Reads source name → maps to AKShare function → normalizes to articles.
    """
    name = "akshare"

    def __init__(self, source_record=None, config: dict = None):
        super().__init__(config)
        self.source_record = source_record  # NewsSource ORM object
        self._func_name = None
        self._col_map = None

    def _resolve_func(self) -> bool:
        """Look up AKShare function for this source name. Returns True if found."""
        if self._func_name:
            return True
        source_name = self.source_record.name if self.source_record else ""
        mapping = FUNCTION_MAP.get(source_name)
        if not mapping:
            logger.warning(f"AkshareConnector: no mapping for source '{source_name}'")
            return False
        self._func_name = mapping["func_name"]
        self._col_map = mapping["col_map"]
        return True

    def fetch(self) -> list[dict]:
        if not self._resolve_func():
            return []

        try:
            import akshare as ak
            func = getattr(ak, self._func_name, None)
            if func is None:
                logger.error(f"AkshareConnector: ak.{self._func_name}() not found")
                return []
        except ImportError:
            logger.error("AkshareConnector: akshare not installed")
            return []

        try:
            df = func()
        except Exception as e:
            logger.error(f"AkshareConnector: ak.{self._func_name}() failed: {e}")
            return []

        if df is None or df.empty:
            logger.info(f"AkshareConnector: ak.{self._func_name}() returned empty")
            return []

        articles = []
        source_display = self.source_record.display_name if self.source_record else "AKShare"

        for _, row in df.iterrows():
            title = self._extract_title(row)
            content = self._extract_content(row)
            published_at = self._extract_time(row)

            article = {
                "title": str(title)[:200] if title else "无标题",
                "url": self._make_url(title, row),
                "content": str(content)[:5000] if content else str(title)[:500],
                "summary": str(content)[:500] if content else str(title)[:200],
                "published_at": published_at or datetime.now(timezone.utc),
                "metadata": {
                    "source": source_display,
                    "source_type": "akshare",
                    "raw_columns": list(row.index)[:20],
                },
            }
            article["content_hash"] = hashlib.sha256(article["url"].encode()).hexdigest()
            articles.append(article)

        logger.info(f"Akshare [{self._func_name}]: {len(articles)} articles")
        return articles

    def _extract_title(self, row) -> str:
        cm = self._col_map
        if "title" in cm:
            val = row.get(cm["title"])
            if val and str(val).strip():
                return str(val).strip()
        # Fallback: use content first 80 chars as title
        content = self._extract_content(row)
        return str(content)[:80] if content else "AKShare新闻"

    def _extract_content(self, row) -> str:
        cm = self._col_map
        if "content" in cm:
            val = row.get(cm["content"])
            if val and str(val).strip():
                return str(val).strip()
        # Fallback: try common column names
        for col in ("content", "内容", "摘要", "summary", "text", "body"):
            if col in row.index:
                val = row.get(col)
                if val and str(val).strip():
                    return str(val).strip()
        # Last resort: concatenate all columns
        import pandas as pd
        return " | ".join(str(v) for v in row.values if pd.notna(v))[:5000]

    def _extract_time(self, row) -> datetime | None:
        import re
        cm = self._col_map
        col_name = cm.get("published_at", cm.get("date", "date"))
        val = row.get(col_name) if col_name in row.index else None
        if val is None:
            return None
        s = str(val).strip()
        # Try common formats
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y%m%d",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                pass
        # ISO
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
        return None

    def _make_url(self, title, row) -> str:
        """Generate a synthetic URL from content hash (no real URL in AKShare)."""
        raw = str(title) + str(row.to_dict())
        h = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"akshare://{self._func_name}/{h}"

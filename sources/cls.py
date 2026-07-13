"""新浪财经 (Sina Finance) news source connector."""
import hashlib
import json
import logging
from datetime import datetime, timezone

import requests

from sources.base import AbstractSourceConnector
from sources.registry import register_connector

logger = logging.getLogger("source.cls")

# Sina Finance roll API — free, no key needed, returns Chinese financial news
SINA_API = (
    "https://feed.mix.sina.com.cn/api/roll/get"
    "?pageid=153&lid=2509&k=&num=30&page=1"
)


@register_connector("cls")
class CLSConnector(AbstractSourceConnector):
    """Sina Finance news connector (Chinese financial news).

    Note: Originally designed for cls.cn RSS, but their RSS/API now requires
    authentication. Switched to Sina Finance public API which is free and reliable.
    """
    name = "cls"

    def fetch(self) -> list[dict]:
        """Fetch articles from Sina Finance roll API."""
        articles = []
        try:
            resp = requests.get(
                SINA_API,
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            items = []
            if isinstance(data, dict):
                result = data.get("result", {})
                items = result.get("data", [])

            for item in items:
                if not isinstance(item, dict):
                    continue

                title = item.get("title", "").strip()
                url = item.get("url", "") or item.get("wapurl", "")
                summary = item.get("intro", "") or item.get("summary", "")
                media_name = item.get("media_name", "")
                ctime = item.get("ctime", "")

                if not title or not url:
                    continue

                # Parse ctime — Sina uses a custom timestamp format
                published_at = datetime.now(timezone.utc)
                if ctime and ctime.isdigit() and len(ctime) == 10:
                    try:
                        published_at = datetime.fromtimestamp(int(ctime), tz=timezone.utc)
                    except (ValueError, OSError):
                        pass

                article = {
                    "title": title[:200],
                    "url": url,
                    "content": summary or title,
                    "summary": summary[:500] if summary else title[:200],
                    "published_at": published_at,
                    "metadata": {
                        "source": f"新浪财经-{media_name}" if media_name else "新浪财经",
                        "source_type": "api",
                        "raw_id": item.get("docid", ""),
                        "media_name": media_name,
                    },
                }
                article["content_hash"] = hashlib.sha256(
                    article["url"].encode()
                ).hexdigest()
                articles.append(article)

            logger.info(f"新浪财经 API: fetched {len(articles)} articles")

        except requests.RequestException as e:
            logger.error(f"新浪财经 API request failed: {e}")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"新浪财经 API parse failed: {e}")
        except Exception as e:
            logger.error(f"新浪财经 API unexpected error: {e}")

        return articles

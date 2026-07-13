"""Generic connector for user-added sources (api / webpage / rss)."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import requests

from sources.base import AbstractSourceConnector

logger = logging.getLogger("source.generic")

API_TIMEOUT = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/html, application/xml, */*",
}

# Common JSON keys that might contain a list of articles
LIST_CANDIDATE_KEYS = (
    "data", "items", "results", "articles", "list", "news",
    "posts", "entries", "records", "content",
)

# Common field name mappings for article normalization
TITLE_KEYS = ("title", "name", "headline", "subject", "caption")
URL_KEYS = ("url", "link", "href", "uri", "permalink", "web_url")
CONTENT_KEYS = ("content", "body", "text", "description", "summary", "intro", "abstract")
SUMMARY_KEYS = ("summary", "intro", "description", "abstract", "excerpt", "digest")
TIME_KEYS = ("published_at", "created_at", "pub_date", "date", "timestamp", "ctime", "updated_at", "display_time")


class GenericConnector(AbstractSourceConnector):
    """Best-effort connector for any user-added source.

    Reads source_type + base_url from the NewsSource DB record.
    Supports: api (JSON endpoint), webpage (HTML crawl), rss (XML feed).
    """
    name = "generic"

    def __init__(self, source_record=None, config: dict = None):
        super().__init__(config)
        self.source_record = source_record  # NewsSource ORM object

    # ── Public API ──────────────────────────────────────────────────────
    def fetch(self) -> list[dict]:
        if not self.source_record:
            logger.warning("GenericConnector: no source_record provided")
            return []

        url = (self.source_record.base_url or "").strip()
        if not url:
            logger.warning(f"GenericConnector: empty URL for source '{self.source_record.name}'")
            return []

        source_type = (self.source_record.source_type or "api").strip().lower()

        try:
            if source_type == "api":
                return self._fetch_api(url)
            elif source_type == "webpage":
                return self._fetch_webpage(url)
            elif source_type == "rss":
                return self._fetch_rss(url)
            else:
                logger.warning(f"GenericConnector: unknown source_type '{source_type}'")
                return self._fetch_api(url)  # fallback
        except Exception as e:
            logger.error(f"GenericConnector fetch failed for '{url}': {e}")
            return []

    # ── API fetcher ─────────────────────────────────────────────────────
    def _fetch_api(self, url: str) -> list[dict]:
        articles = []
        try:
            resp = requests.get(url, timeout=API_TIMEOUT, headers=HEADERS)
            resp.raise_for_status()

            # Try JSON first, fall back to text
            ct = resp.headers.get("content-type", "")
            if "application/json" not in ct and "text" not in ct.lower():
                logger.debug(f"Generic API: unexpected content-type '{ct}' for {url}")

            try:
                data = resp.json()
            except (ValueError, requests.JSONDecodeError):
                # Not JSON — treat as single webpage article
                return self._text_as_article(resp.text, url)

            items = self._find_article_list(data)

            for item in items:
                article = self._normalize_item(item, url)
                if article:
                    articles.append(article)

            logger.info(f"Generic API [{url[:60]}]: {len(articles)} articles extracted")

        except requests.RequestException as e:
            logger.error(f"Generic API request failed [{url[:80]}]: {e}")
        except Exception as e:
            logger.error(f"Generic API unexpected error [{url[:80]}]: {e}")

        return articles

    # ── Webpage fetcher ─────────────────────────────────────────────────
    def _fetch_webpage(self, url: str) -> list[dict]:
        """Smart crawl — auto-detect article lists, fallback to single page."""
        try:
            from sources.smart_crawler import SmartCrawlerConnector
            crawler = SmartCrawlerConnector(
                source_record=self.source_record, config=self.config
            )
            articles = crawler.fetch()
            if articles:
                return articles
        except ImportError:
            logger.error("smart_crawler not available")
        except Exception as e:
            logger.error(f"SmartCrawler failed [{url[:80]}]: {e}")

        # Fallback: treat as single article via legacy web_crawler
        try:
            from assistant.tools.web_crawler import crawl_url
            result = crawl_url(url, max_length=5000)
            if result["success"] and result["content_length"] > 50:
                article = {
                    "title": result["title"] or url,
                    "url": url,
                    "content": result["content"],
                    "summary": result["content"][:500] if result["content"] else "",
                    "published_at": datetime.now(timezone.utc),
                    "metadata": {
                        "source": self.source_record.display_name if self.source_record else url,
                        "source_type": "webpage",
                        "method": result.get("method", "unknown"),
                    },
                }
                article["content_hash"] = hashlib.sha256(url.encode()).hexdigest()
                return [article]
        except ImportError:
            logger.error("web_crawler not available for webpage source")
        except Exception as e:
            logger.error(f"Webpage fetch failed [{url[:80]}]: {e}")
        return []

    # ── RSS / Atom fetcher ──────────────────────────────────────────────
    def _fetch_rss(self, url: str) -> list[dict]:
        """Parse RSS 2.0 / Atom feed."""
        articles = []
        try:
            import xml.etree.ElementTree as ET

            resp = requests.get(url, timeout=API_TIMEOUT, headers={
                **HEADERS, "Accept": "application/xml, application/rss+xml, application/atom+xml, */*"
            })
            resp.raise_for_status()

            # Some servers return RSS with wrong content-type
            raw = resp.text
            if not raw.strip().startswith("<?xml") and not raw.strip().startswith("<"):
                # Maybe JSON — delegate to API fetcher
                return self._fetch_api(url)

            root = ET.fromstring(raw)

            # Detect feed type
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            is_atom = root.tag.endswith("feed") or root.tag == "{http://www.w3.org/2005/Atom}feed"

            if is_atom:
                entries = root.findall("atom:entry", ns) or root.findall("entry")
            else:
                # RSS 2.0: <channel> → <item>
                channel = root.find("channel")
                entries = channel.findall("item") if channel is not None else root.findall("item")

            for entry in entries:
                article = self._parse_xml_entry(entry, is_atom, ns)
                if article and article.get("title"):
                    article["url"] = article.get("url") or f"{url}#{hash(article['title'])}"
                    article["content_hash"] = hashlib.sha256(article["url"].encode()).hexdigest()
                    articles.append(article)

            logger.info(f"Generic RSS [{url[:60]}]: {len(articles)} entries")

        except ET.ParseError as e:
            logger.error(f"XML parse failed [{url[:80]}]: {e}")
        except requests.RequestException as e:
            logger.error(f"RSS request failed [{url[:80]}]: {e}")
        except Exception as e:
            logger.error(f"RSS unexpected error [{url[:80]}]: {e}")

        return articles

    # ── Helpers ─────────────────────────────────────────────────────────
    def _find_article_list(self, data) -> list:
        """Search common JSON paths for an article list."""
        if isinstance(data, list):
            return data

        if not isinstance(data, dict):
            return []

        # Direct list under a known key
        for key in LIST_CANDIDATE_KEYS:
            val = data.get(key)
            if isinstance(val, list) and len(val) > 0:
                return val

        # Nested: e.g. {"data": {"items": [...]}}
        for outer in LIST_CANDIDATE_KEYS:
            outer_val = data.get(outer)
            if isinstance(outer_val, dict):
                for inner in LIST_CANDIDATE_KEYS:
                    inner_val = outer_val.get(inner)
                    if isinstance(inner_val, list) and len(inner_val) > 0:
                        return inner_val

        # No list found — wrap the whole dict as a single item
        if data:
            return [data]
        return []

    def _normalize_item(self, item, source_url: str) -> dict | None:
        """Map an arbitrary JSON object to the standard article format."""
        if not isinstance(item, dict):
            return None

        title = self._first_of(item, TITLE_KEYS)
        if not title or not str(title).strip():
            return None

        url = self._first_of(item, URL_KEYS) or ""
        content = self._first_of(item, CONTENT_KEYS) or title
        summary = self._first_of(item, SUMMARY_KEYS) or str(content)[:500]
        ts = self._first_of(item, TIME_KEYS)
        published_at = self._parse_timestamp(ts)

        source_display = self.source_record.display_name if self.source_record else "自定义源"

        article = {
            "title": str(title)[:200],
            "url": str(url) if url else f"{source_url}#{hash(str(title))}",
            "content": str(content) if content else str(title),
            "summary": str(summary)[:500] if summary else str(title)[:200],
            "published_at": published_at,
            "metadata": {
                "source": source_display,
                "source_type": self.source_record.source_type if self.source_record else "api",
                "raw_keys": list(item.keys())[:20],
            },
        }
        article["content_hash"] = hashlib.sha256(article["url"].encode()).hexdigest()
        return article

    @staticmethod
    def _first_of(item: dict, keys: tuple) -> str | None:
        for k in keys:
            val = item.get(k)
            if val is not None and str(val).strip():
                return str(val).strip()
        return None

    @staticmethod
    def _parse_timestamp(value) -> datetime:
        """Best-effort timestamp parsing."""
        if value is None:
            return datetime.now(timezone.utc)

        if isinstance(value, (int, float)):
            # Unix timestamp (seconds or milliseconds)
            try:
                if value > 1_000_000_000_000:
                    value = value / 1000
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except (ValueError, OSError):
                pass

        if isinstance(value, str):
            value = value.strip()
            # ISO format
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
            # Common formats
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S %Z",
            ):
                try:
                    return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

        return datetime.now(timezone.utc)

    def _text_as_article(self, text: str, url: str) -> list[dict]:
        """Wrap raw text content as a single article (fallback for non-JSON APIs)."""
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(text, "lxml")
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else url
            body = soup.get_text("\n", strip=True)[:5000]
        except Exception:
            title = url
            body = text[:5000]

        article = {
            "title": title[:200],
            "url": url,
            "content": body,
            "summary": body[:500],
            "published_at": datetime.now(timezone.utc),
            "metadata": {"source": self.source_record.display_name if self.source_record else url, "source_type": "api"},
        }
        article["content_hash"] = hashlib.sha256(url.encode()).hexdigest()
        logger.info(f"Generic API: non-JSON response treated as single article")
        return [article]

    def _parse_xml_entry(self, entry, is_atom: bool, ns: dict) -> dict | None:
        """Parse a single RSS item or Atom entry into a dict."""
        if is_atom:
            title_el = entry.find("atom:title", ns) or entry.find("title")
            link_el = entry.find("atom:link", ns) or entry.find("link")
            summary_el = entry.find("atom:summary", ns) or entry.find("summary")
            updated_el = entry.find("atom:updated", ns) or entry.find("updated")
            url = link_el.attrib.get("href", "") if link_el is not None else ""
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            content = summary_el.text.strip() if summary_el is not None and summary_el.text else ""
            summary = content[:500] if content else ""
            ts = updated_el.text.strip() if updated_el is not None and updated_el.text else None
        else:
            title = getattr(entry.find("title"), "text", "") if entry.find("title") is not None else ""
            link = entry.find("link")
            url = link.text.strip() if link is not None and link.text else ""
            desc = entry.find("description")
            content = desc.text.strip() if desc is not None and desc.text else ""
            summary = content[:500]
            pub_date = entry.find("pubDate")
            ts = pub_date.text.strip() if pub_date is not None and pub_date.text else None

        if not title:
            return None

        return {
            "title": str(title)[:200],
            "url": str(url) if url else "",
            "content": content or title,
            "summary": str(summary)[:500] if summary else str(title)[:200],
            "published_at": self._parse_timestamp(ts),
            "metadata": {"source": self.source_record.display_name if self.source_record else "RSS", "source_type": "rss"},
        }

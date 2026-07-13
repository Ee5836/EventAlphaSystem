"""Web search tool for AI Assistant — DuckDuckGo-based search."""
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def web_search(query: str, max_results: int = 5, timeout: int = 10) -> list[dict]:
    """Perform a web search and return results.

    Uses DuckDuckGo Instant Answer API (no API key required).
    Falls back to a simple HTTP-based approach.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (1-10).
        timeout: Request timeout in seconds.

    Returns:
        List of dicts with keys: title, url, snippet, source.
    """
    results = []

    try:
        # DuckDuckGo Instant Answer API
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
            },
            timeout=timeout,
            headers={"User-Agent": "BubbleEvent/1.0 Research Assistant"}
        )
        resp.raise_for_status()
        data = resp.json()

        # Abstract (main answer)
        if data.get("AbstractText"):
            results.append({
                "title": data.get("AbstractSource", "DuckDuckGo"),
                "url": data.get("AbstractURL", ""),
                "snippet": data["AbstractText"],
                "source": "ddg_abstract",
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and "Text" in topic:
                results.append({
                    "title": topic.get("FirstURL", "").split("/")[-1].replace("_", " ")[:80],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", ""),
                    "source": "ddg_related",
                })

        # If DuckDuckGo returned insufficient results, try HTML scraping
        if len(results) < max_results:
            html_results = _search_ddg_html(query, max_results - len(results), timeout)
            results.extend(html_results)

    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}, trying HTML fallback")
        try:
            results = _search_ddg_html(query, max_results, timeout)
        except Exception as e2:
            logger.error(f"All search methods failed: {e2}")

    # Deduplicate by URL
    seen = set()
    unique = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return unique[:max_results]


def _search_ddg_html(query: str, max_results: int, timeout: int) -> list[dict]:
    """Fallback: scrape DuckDuckGo HTML search results."""
    try:
        from bs4 import BeautifulSoup

        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=timeout,
            headers={"User-Agent": "BubbleEvent/1.0 Research Assistant"}
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        results = []
        for item in soup.select(".result")[:max_results]:
            link = item.select_one(".result__a")
            snippet = item.select_one(".result__snippet")
            if link:
                results.append({
                    "title": link.get_text(strip=True),
                    "url": link.get("href", ""),
                    "snippet": snippet.get_text(strip=True) if snippet else "",
                    "source": "ddg_html",
                })
        return results
    except ImportError:
        logger.warning("BeautifulSoup not available for HTML search fallback")
        return []

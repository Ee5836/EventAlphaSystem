"""Smart web crawler — auto-detect article lists from any URL.

Algorithm:
1. Parse HTML → find repeating DOM blocks (candidate article containers)
2. Score each block: has link? has heading? content length? structure consistency?
3. Extract title/url/summary/time per article
4. Fallback: user-provided CSS selectors → single-page text extraction

Performance: static httpx (fast) → Playwright fallback (JS-heavy pages).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from sources.base import AbstractSourceConnector
from sources.registry import register_connector

logger = logging.getLogger("source.crawler")

TIMEOUT = 15
MAX_ARTICLES = 50

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# ── Common CSS selectors for known site layouts ────────────────────────
KNOWN_CONTAINER_SELECTORS = [
    # News list patterns (ordered by specificity)
    "article", ".article", ".post", ".news-item", ".news_item",
    "li:has(a)", ".item", ".list-item", ".card",
    # Generic repeating patterns
    "ul > li", "ol > li",
    "div > div:has(a)",
    ".content > div",
]

# Selectors to find title within a container
TITLE_SELECTORS = [
    "h1", "h2", "h3", "h4", "h5",
    ".title", ".headline", ".heading",
    "a",  # last resort
]

# Selectors to find content/summary
CONTENT_SELECTORS = [
    "p", ".summary", ".description", ".excerpt", ".abstract",
    ".content", ".body", ".text", ".intro",
]


@register_connector("smart_crawler")
class SmartCrawlerConnector(AbstractSourceConnector):
    """Auto-detect & extract news articles from any webpage URL.

    User can optionally provide CSS selectors in source config:
        config_json: {
            "container_selector": ".news-list > li",   # optional
            "title_selector": "h3.title",               # optional
            "url_selector": "a.link",                   # optional
            "content_selector": "p.summary",            # optional
            "time_selector": "time.pub-date",           # optional
            "next_page_selector": "a.next",             # optional
            "max_pages": 3,                              # optional
        }
    """
    name = "smart_crawler"

    def __init__(self, source_record=None, config: dict = None):
        super().__init__(config)
        self.source_record = source_record  # NewsSource ORM object

    # ── Public API ──────────────────────────────────────────────────────
    def fetch(self) -> list[dict]:
        url = self._get_url()
        if not url:
            return []

        # Read user-provided selectors from config
        cfg = (self.source_record.config_json or {}) if self.source_record else {}
        selectors = {
            "container": cfg.get("container_selector"),
            "title": cfg.get("title_selector"),
            "url": cfg.get("url_selector"),
            "content": cfg.get("content_selector"),
            "time": cfg.get("time_selector"),
        }
        max_pages = int(cfg.get("max_pages", 3))

        all_articles = []
        seen_urls = set()

        for page in range(max_pages):
            page_url = url if page == 0 else self._next_page(url, page, cfg)
            if not page_url:
                break

            soup = self._fetch_page(page_url)
            if soup is None:
                break

            articles = self._extract_articles(soup, page_url, selectors)
            new_articles = 0
            for a in articles:
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    all_articles.append(a)
                    new_articles += 1

            logger.info(
                f"SmartCrawler [{url[:60]}]: page {page+1} → "
                f"{len(articles)} found, {new_articles} new"
            )

            if new_articles == 0 or page >= max_pages - 1:
                break

        source_display = self.source_record.display_name if self.source_record else url
        for a in all_articles:
            a["metadata"]["source"] = source_display

        logger.info(f"SmartCrawler total: {len(all_articles)} articles from {url[:60]}")
        return all_articles[:MAX_ARTICLES]

    # ── Page fetching ───────────────────────────────────────────────────
    def _get_url(self) -> str:
        if not self.source_record:
            return ""
        return (self.source_record.base_url or "").strip()

    def _fetch_page(self, url: str) -> BeautifulSoup | None:
        """Fetch a page. Static first → Playwright fallback."""
        # Try static
        soup = self._fetch_static(url)
        if soup and self._has_substantial_content(soup):
            return soup

        # Fallback to dynamic
        logger.debug(f"Static crawl thin, trying Playwright for {url[:60]}")
        return self._fetch_dynamic(url)

    def _fetch_static(self, url: str) -> BeautifulSoup | None:
        try:
            resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "application/json" in ct and "text/html" not in ct:
                logger.debug(f"Skipping JSON endpoint: {url[:60]}")
                return None
            # Respect encoding
            if resp.encoding and resp.encoding.lower() != "utf-8":
                resp.encoding = resp.apparent_encoding or resp.encoding
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as e:
            logger.error(f"Static fetch failed [{url[:60]}]: {e}")
            return None
        except Exception as e:
            logger.error(f"Parse error [{url[:60]}]: {e}")
            return None

    def _fetch_dynamic(self, url: str) -> BeautifulSoup | None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=[
                    "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                ])
                try:
                    page = browser.new_page()
                    # Block heavy resources
                    page.route("**/*.{png,jpg,jpeg,gif,svg,mp4,mp3,woff,woff2,ttf,font}",
                               lambda route: route.abort())
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    html = page.content()
                    return BeautifulSoup(html, "lxml")
                finally:
                    browser.close()
        except ImportError:
            logger.debug("Playwright not installed, static-only mode")
            return None
        except Exception as e:
            logger.warning(f"Dynamic fetch failed [{url[:60]}]: {e}")
            return None

    @staticmethod
    def _has_substantial_content(soup: BeautifulSoup) -> bool:
        """Check if page has enough text to be worth analyzing."""
        body = soup.find("body")
        if not body:
            return False
        text = body.get_text(strip=True)
        return len(text) > 300

    # ── Article extraction ──────────────────────────────────────────────
    def _extract_articles(
        self, soup: BeautifulSoup, base_url: str, selectors: dict
    ) -> list[dict]:
        """Main extraction pipeline."""
        containers = self._find_containers(soup, selectors.get("container"))
        if not containers:
            logger.debug(f"No article containers found at {base_url[:60]}")
            return []

        articles = []
        for c in containers[:MAX_ARTICLES * 2]:  # oversample, then filter
            article = self._extract_one(c, base_url, selectors)
            if article and article.get("title") and self._is_valid_title(article["title"]):
                articles.append(article)
            if len(articles) >= MAX_ARTICLES:
                break

        return articles

    # ── Container detection ─────────────────────────────────────────────
    def _find_containers(self, soup: BeautifulSoup, user_selector: str | None) -> list[Tag]:
        """Find article container elements in the DOM."""
        # Priority 1: User-provided selector
        if user_selector:
            try:
                containers = soup.select(user_selector)
                if containers:
                    logger.debug(f"User selector '{user_selector}' → {len(containers)} containers")
                    return containers
            except Exception:
                pass

        # Priority 2: Auto-detect by scoring repeating blocks
        candidates = self._auto_detect_containers(soup)
        if candidates:
            return candidates

        # Priority 3: Known selectors
        for sel in KNOWN_CONTAINER_SELECTORS:
            try:
                containers = soup.select(sel)
                if len(containers) >= 3:
                    logger.debug(f"Known selector '{sel}' → {len(containers)} containers")
                    return containers
            except Exception:
                continue

        return []

    def _auto_detect_containers(self, soup: BeautifulSoup) -> list[Tag]:
        """Score DOM subtrees to find repeating article-like blocks.

        Strategy:
        1. Find all elements that contain both a link and text
        2. Group by parent → score by child count & structure similarity
        3. Return the best-scoring group
        """
        body = soup.find("body")
        if not body:
            return []

        # Phase 1: Find all "leaf containers" — elements with link + text
        leaf_containers: list[Tag] = []
        for tag in body.find_all(["div", "li", "article", "section"]):
            links = tag.find_all("a", href=True)
            if not links:
                continue
            # Must have some meaningful text beyond just the link
            text_len = len(tag.get_text(strip=True))
            if text_len < 20:
                continue
            # Skip obvious non-article containers (nav, footer, header, aside)
            parent_classes = self._get_parent_classes(tag)
            if any(skip in parent_classes for skip in
                   ("nav", "footer", "header", "sidebar", "menu", "comment", "ad", "banner")):
                continue
            leaf_containers.append(tag)

        if not leaf_containers:
            return []

        # Phase 2: Group by immediate parent → find repeating patterns
        parent_groups: dict[str, list[Tag]] = {}
        for c in leaf_containers:
            if c.parent:
                key = self._tag_path(c.parent)
                parent_groups.setdefault(key, []).append(c)

        # Phase 3: Score each group
        scored = []
        for key, children in parent_groups.items():
            if len(children) < 3:
                continue
            score = self._score_group(children)
            scored.append((score, children))

        scored.sort(key=lambda x: x[0], reverse=True)

        if scored:
            best_score, best_children = scored[0]
            logger.debug(
                f"Auto-detected {len(best_children)} containers (score={best_score:.1f})"
            )
            return best_children

        # Fallback: if we found leaf containers but couldn't group well,
        # return the top 10 highest-quality ones
        if leaf_containers:
            scored_single = [
                (self._score_single(c), c) for c in leaf_containers
            ]
            scored_single.sort(key=lambda x: x[0], reverse=True)
            top = [c for _, c in scored_single[:20]]
            if len(top) >= 3:
                return top

        return []

    def _score_group(self, children: list[Tag]) -> float:
        """Score a group of sibling containers for article-likeliness."""
        if not children:
            return 0

        score = float(len(children)) * 2.0  # base: more = better pattern

        for c in children:
            score += self._score_single(c) * 0.5  # individual quality

        # Bonus: structural consistency (similar child count → repeating pattern)
        child_counts = [len(list(c.children)) for c in children]
        if len(set(child_counts)) <= len(children) // 2:
            score += 5.0

        return score

    def _score_single(self, tag: Tag) -> float:
        """Score a single element for article-likeliness."""
        score = 0.0
        text = tag.get_text(strip=True)
        text_len = len(text)

        # Link presence
        links = tag.find_all("a", href=True)
        has_link = len(links) > 0
        if has_link:
            score += 3.0
        if len(links) >= 2:
            score += 1.0

        # Text quality
        if text_len > 50:
            score += 3.0
        elif text_len > 20:
            score += 1.5

        # Heading presence
        headings = tag.find_all(["h1", "h2", "h3", "h4", "h5"])
        if headings:
            score += 2.0

        # Image presence (many article cards have images)
        if tag.find("img"):
            score += 0.5

        # Time element presence
        if tag.find("time") or tag.find(attrs={"datetime": True}):
            score += 1.0

        # Penalize if looks like navigation
        link_text_ratio = sum(len(a.get_text(strip=True)) for a in links) / max(text_len, 1)
        if link_text_ratio > 0.8:
            score -= 3.0

        # Penalize very long text (more likely a body/article, not a card)
        if text_len > 2000:
            score -= 2.0

        return score

    @staticmethod
    def _get_parent_classes(tag: Tag) -> str:
        """Get class names from tag and all its parents (for skip-list matching)."""
        classes = []
        current = tag
        while current and hasattr(current, 'name'):
            if current.get("class"):
                classes.extend(current["class"])
            if current.get("id"):
                classes.append(current["id"])
            current = current.parent
        return " ".join(classes).lower()

    @staticmethod
    def _tag_path(tag: Tag) -> str:
        """Stable key for a tag: tag_name#id.class1.class2."""
        parts = [tag.name]
        if tag.get("id"):
            parts.append(f"#{tag['id']}")
        if tag.get("class"):
            parts.extend(f".{c}" for c in tag["class"])
        return "".join(parts)

    # ── Single article extraction ───────────────────────────────────────
    def _extract_one(
        self, container: Tag, base_url: str, selectors: dict
    ) -> dict | None:
        """Extract title, url, content from a single container element."""
        title = self._extract_field(container, selectors.get("title"), TITLE_SELECTORS, "text")
        article_url = self._extract_field(container, selectors.get("url"), ["a"], "href")
        content = self._extract_field(container, selectors.get("content"), CONTENT_SELECTORS, "text")
        time_val = self._extract_field(container, selectors.get("time"), ["time", "[datetime]"], "time")

        # Fallback: if no title found, use first substantial text
        if not title:
            text = container.get_text(strip=True)[:100]
            if text:
                title = text

        if not title or len(str(title)) < 3:
            return None

        # Resolve URL
        if article_url:
            article_url = urljoin(base_url, str(article_url))
        else:
            # Try find any link
            link = container.find("a", href=True)
            if link:
                article_url = urljoin(base_url, link["href"])
            else:
                h = hashlib.sha256(str(title).encode()).hexdigest()[:12]
                article_url = f"{base_url}#{h}"

        # Normalize content
        content_text = str(content or "")[:5000] if content else str(title)
        summary_text = str(content or title)[:500]

        # Parse time
        published_at = None
        if time_val:
            published_at = self._parse_time(str(time_val))

        source_display = self.source_record.display_name if self.source_record else base_url

        article = {
            "title": str(title)[:200],
            "url": article_url,
            "content": content_text,
            "summary": summary_text,
            "published_at": published_at or datetime.now(timezone.utc),
            "metadata": {
                "source": source_display,
                "source_type": "webpage",
                "method": "smart_crawler",
            },
        }
        article["content_hash"] = hashlib.sha256(article_url.encode()).hexdigest()
        return article

    def _extract_field(
        self, container: Tag, user_sel: str | None,
        fallback_sels: list[str], mode: str
    ) -> str | None:
        """Extract a field from container using user selector or fallbacks.

        mode: "text" → return inner text, "href" → return href attr, "time" → return datetime attr
        """
        # Try user selector first
        if user_sel:
            try:
                el = container.select_one(user_sel)
                if el:
                    return self._value_from_el(el, mode)
            except Exception:
                pass

        # Try fallback selectors
        for sel in fallback_sels:
            try:
                if sel == "[datetime]":
                    el = container.find(attrs={"datetime": True})
                else:
                    el = container.select_one(sel)
                if el:
                    val = self._value_from_el(el, mode)
                    if val:
                        return val
            except Exception:
                continue

        return None

    @staticmethod
    def _value_from_el(el: Tag, mode: str) -> str | None:
        if mode == "href":
            return el.get("href")
        elif mode == "time":
            return el.get("datetime")
        else:  # text
            return el.get_text(strip=True)

    @staticmethod
    def _parse_time(s: str) -> datetime | None:
        """Parse common timestamp formats."""
        s = s.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%a, %d %b %Y %H:%M:%S %z",
        ):
            try:
                return datetime.strptime(s[:len(fmt)], fmt).replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                pass
        # ISO
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _is_valid_title(title: str) -> bool:
        """Reject obvious non-article titles (nav, brand names, etc.)."""
        t = title.strip()
        if len(t) < 4:
            return False
        # Blacklist: common nav/brand junk
        blacklist = (
            "关于我们", "联系我们", "首页", "登录", "注册",
            "更多", "查看更多", "详情", "阅读全文", "查看详情",
            "上一篇", "下一篇", "返回", "顶部",
            "about us", "contact", "home", "login", "sign up",
            "copyright", "privacy policy", "terms",
            "财联社", "cls", "新浪财经", "东方财富",
            "eastmoney", "sina",
        )
        tl = t.lower()
        for word in blacklist:
            if word in tl:
                return False
        # Must contain at least one CJK char or 3+ Latin words
        cjk = sum(1 for c in t if '一' <= c <= '鿿')
        if cjk > 0:
            return True
        word_count = len(t.split())
        if word_count >= 3:
            return True
        return False

    # ── Pagination ──────────────────────────────────────────────────────
    def _next_page(self, url: str, current_page: int, cfg: dict) -> str | None:
        """Try to determine the next page URL."""
        next_sel = cfg.get("next_page_selector")
        if next_sel and current_page == 0:
            soup = self._fetch_static(url)
            if soup:
                try:
                    next_link = soup.select_one(next_sel)
                    if next_link and next_link.get("href"):
                        return urljoin(url, next_link["href"])
                except Exception:
                    pass

        # Try common pagination patterns
        patterns = [
            f"{url.rstrip('/')}/page/{current_page + 1}",
            f"{url.rstrip('/')}?page={current_page + 1}",
            f"{url.rstrip('/')}&page={current_page + 1}",
            f"{url.rstrip('/')}?p={current_page + 1}",
        ]

        # Only try if current_page == 0 (first pagination attempt)
        if current_page == 0:
            for pattern in patterns[:2]:  # limit to 2 pattern attempts
                try:
                    r = requests.head(pattern, timeout=8, headers=HEADERS, allow_redirects=True)
                    if r.status_code == 200:
                        return pattern
                except Exception:
                    pass

        return None

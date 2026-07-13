"""High-performance web crawler — optimized static + dynamic.

Speed optimizations:
1. httpx connection pooling + keep-alive for static crawls
2. ThreadPoolExecutor for parallel static crawling
3. Reusable Playwright browser (sequential, avoids greenlet issues)
4. LRU cache for recently crawled URLs
5. Resource blocking (images/fonts) during dynamic crawl
6. Shorter timeouts with fast-fail
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

STATIC_TIMEOUT = 8
DYNAMIC_TIMEOUT = 12
MAX_STATIC_WORKERS = 8

# ── Shared httpx client (thread-safe) ────────────────────────────────
_httpx_client = None
_httpx_lock = threading.Lock()


def _get_http_client():
    global _httpx_client
    if _httpx_client is None:
        with _httpx_lock:
            if _httpx_client is None:
                import httpx
                _httpx_client = httpx.Client(
                    timeout=httpx.Timeout(STATIC_TIMEOUT),
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    follow_redirects=True,
                )
    return _httpx_client


# ── Playwright singleton (NOT thread-safe, use with lock) ────────────
_playwright_instance = None
_browser_instance = None
_browser_lock = threading.Lock()


def _get_browser():
    global _playwright_instance, _browser_instance
    if _browser_instance is None:
        with _browser_lock:
            if _browser_instance is None:
                try:
                    from playwright.sync_api import sync_playwright
                    _playwright_instance = sync_playwright().start()
                    _browser_instance = _playwright_instance.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-gpu", "--disable-dev-shm-usage",
                            "--no-sandbox", "--disable-extensions",
                            "--disable-background-networking", "--disable-sync",
                            "--disable-translate", "--mute-audio", "--no-first-run",
                        ],
                    )
                except ImportError:
                    return None
                except Exception as e:
                    logger.error(f"Browser launch failed: {e}")
                    return None
    return _browser_instance


# ── Public API ────────────────────────────────────────────────────────
@lru_cache(maxsize=300)
def crawl_url(url: str, timeout: int = 10, max_length: int = 5000) -> dict:
    """Crawl a single URL. Results LRU-cached (300 URLs).

    Strategy: static first (fast, ~0.5-2s) → dynamic fallback (JS pages).
    """
    result = {
        "success": False, "url": url, "title": "", "content": "",
        "content_length": 0, "method": "static", "error": None,
    }

    if not urlparse(url).scheme:
        result["error"] = f"Invalid URL: {url}"
        return result

    # Step 1: Fast static
    sr = _static_crawl(url, max_length)
    if sr["success"] and sr["content_length"] > 200:
        return sr

    # Step 2: Dynamic for thin/empty pages
    if sr["content_length"] < 200:
        dr = _dynamic_crawl(url, max_length)
        if dr["success"]:
            return dr

    if sr["content"]:
        return sr
    result["error"] = sr.get("error") or "Crawl failed"
    return result


def crawl_many(urls: list[str], max_length: int = 5000) -> list[dict]:
    """Crawl multiple URLs efficiently.

    - URLs previously cached → instant return
    - Uncached static pages → parallel crawl via thread pool
    - JS-heavy pages → sequential via shared browser
    """
    if not urls:
        return []

    results = {}

    # Phase 1: Check cache, collect misses
    uncached = []
    for i, url in enumerate(urls):
        # Access lru_cache internals to check hit
        cached = crawl_url(url, max_length=max_length)
        if cached["success"] and cached["content_length"] > 200:
            results[i] = cached
        else:
            uncached.append((i, url))

    if not uncached:
        return [results[i] for i in range(len(urls))]

    # Phase 2: Parallel static crawl for uncached URLs
    static_futures = {}
    with ThreadPoolExecutor(max_workers=min(MAX_STATIC_WORKERS, len(uncached))) as executor:
        for idx, url in uncached:
            static_futures[executor.submit(_static_crawl, url, max_length)] = (idx, url)

        dynamic_needed = []
        for future in as_completed(static_futures):
            idx, url = static_futures[future]
            try:
                sr = future.result(timeout=STATIC_TIMEOUT + 3)
                if sr["success"] and sr["content_length"] > 200:
                    results[idx] = sr
                    # Populate cache
                    crawl_url(url, max_length=max_length)
                else:
                    dynamic_needed.append((idx, url))
            except Exception as e:
                dynamic_needed.append((idx, url))

    # Phase 3: Sequential dynamic crawl
    for idx, url in dynamic_needed:
        dr = _dynamic_crawl(url, max_length)
        results[idx] = dr if dr["success"] else (results.get(idx) or dr)
        crawl_url(url, max_length=max_length)

    return [results.get(i, {
        "success": False, "url": urls[i], "title": "", "content": "",
        "content_length": 0, "method": "error", "error": "unknown",
    }) for i in range(len(urls))]


def clear_cache():
    crawl_url.cache_clear()


def shutdown():
    """Clean up shared resources (browser, http client)."""
    global _browser_instance, _playwright_instance, _httpx_client
    try:
        if _browser_instance:
            _browser_instance.close()
    except Exception:
        pass
    try:
        if _playwright_instance:
            _playwright_instance.stop()
    except Exception:
        pass
    _browser_instance = None
    _playwright_instance = None
    try:
        if _httpx_client:
            _httpx_client.close()
    except Exception:
        pass
    _httpx_client = None


# ── Static crawl ──────────────────────────────────────────────────────
def _static_crawl(url: str, max_length: int) -> dict:
    result = {
        "success": False, "url": url, "title": "", "content": "",
        "content_length": 0, "method": "static", "error": None,
    }
    try:
        from bs4 import BeautifulSoup
        client = _get_http_client()
        resp = client.get(url)
        resp.raise_for_status()

        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            result["error"] = f"JSON endpoint, not HTML"
            return result

        soup = BeautifulSoup(resp.text, "lxml")

        title_tag = soup.find("title")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)

        # Priority extraction
        parts = []
        main = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
        root = main or soup
        for tag in root.find_all(["p", "h1", "h2", "h3", "h4"]):
            text = tag.get_text(strip=True)
            if len(text) > 20:
                parts.append(text)
                if sum(len(p) for p in parts) >= max_length:
                    break

        content = "\n".join(parts)[:max_length]
        result["content"] = content
        result["content_length"] = len(content)
        result["success"] = len(content) > 50

    except ImportError as e:
        result["error"] = f"Missing dep: {e}"
    except Exception as e:
        result["error"] = str(e)[:200]

    return result


# ── Dynamic crawl ─────────────────────────────────────────────────────
def _dynamic_crawl(url: str, max_length: int) -> dict:
    result = {
        "success": False, "url": url, "title": "", "content": "",
        "content_length": 0, "method": "playwright", "error": None,
    }

    browser = _get_browser()
    if browser is None:
        result["error"] = "Playwright not available"
        return result

    page = None
    try:
        page = browser.new_page()
        page.set_default_timeout(DYNAMIC_TIMEOUT * 1000)

        # Block heavy resources for speed
        page.route("**/*.{png,jpg,jpeg,gif,svg,mp4,mp3,woff,woff2,ttf,font}",
                   lambda route: route.abort())

        page.goto(url, wait_until="domcontentloaded")
        result["title"] = page.title() or ""

        # Fast text extraction
        content = page.evaluate("""
            () => {
                const skip = new Set(['SCRIPT','STYLE','NAV','FOOTER','HEADER','NOSCRIPT','IFRAME','SVG']);
                const root = document.querySelector('article, main, [role="main"]') || document.body;
                if (!root) return '';
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                const parts = [];
                let node;
                while (node = walker.nextNode()) {
                    const p = node.parentElement;
                    if (!p || skip.has(p.tagName)) continue;
                    const t = node.textContent.trim();
                    if (t.length > 15) parts.push(t);
                    if (parts.length > 200) break;
                }
                return parts.join('\\n');
            }
        """)

        content = content.strip()[:max_length]
        result["content"] = content
        result["content_length"] = len(content)
        result["success"] = len(content) > 100

    except ImportError:
        result["error"] = "Playwright not installed"
    except Exception as e:
        err = str(e)[:200]
        result["error"] = f"Timeout after {DYNAMIC_TIMEOUT}s" if "Timeout" in err else err
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass

    return result

"""Seed system data — default news sources, etc."""
from models.source import NewsSource
from app.extensions import db

SYSTEM_SOURCES = [
    # ── Dedicated connectors ────────────────────────────────────────
    {
        "name": "cls",
        "display_name": "新浪财经",
        "base_url": "https://finance.sina.com.cn",
        "source_type": "api",
        "credibility": 0.70,
        "poll_interval": 1800,
        "tags_json": ["财经", "A股", "快讯", "中国"],
    },
    {
        "name": "36kr",
        "display_name": "36氪快讯",
        "base_url": "https://36kr.com/feed",
        "source_type": "rss",
        "credibility": 0.65,
        "poll_interval": 1800,
        "tags_json": ["科技", "创投", "快讯", "新经济"],
    },
    # ── AKShare-based sources ───────────────────────────────────────
    {
        "name": "ak_cctv",
        "display_name": "央视新闻",
        "base_url": "akshare://news_cctv",
        "source_type": "api",
        "credibility": 0.88,
        "poll_interval": 3600,
        "tags_json": ["央视", "官方", "宏观", "政策"],
    },
    {
        "name": "ak_futures",
        "display_name": "上期所快讯",
        "base_url": "akshare://futures_news_shmet",
        "source_type": "api",
        "credibility": 0.75,
        "poll_interval": 1800,
        "tags_json": ["期货", "大宗商品", "快讯", "国际"],
    },
]


def seed_system_sources():
    """Create default system sources if they don't exist."""
    existing = {s.name for s in NewsSource.query.filter_by(is_system=True).all()}
    created = 0
    for src in SYSTEM_SOURCES:
        if src["name"] not in existing:
            source = NewsSource(
                name=src["name"],
                display_name=src["display_name"],
                base_url=src["base_url"],
                source_type=src["source_type"],
                credibility=src["credibility"],
                is_system=True,
                created_by="system",
                poll_interval=src["poll_interval"],
                tags_json=src["tags_json"],
            )
            db.session.add(source)
            created += 1
    if created:
        db.session.commit()
    return created

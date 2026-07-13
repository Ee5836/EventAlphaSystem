"""Health check and index routes."""
from datetime import date, datetime, timezone, timedelta
from flask import Blueprint, jsonify, render_template
from app.extensions import db
from models.card import EventCard
from models.source import NewsSource, RawArticle
from models.briefing import DailyBriefing

bp = Blueprint("health", __name__)


@bp.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "BubbleEvent"})


@bp.route("/")
def index():
    """Homepage — investment dashboard."""

    # Stats
    today = date.today()
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

    today_events = EventCard.query.filter(
        EventCard.created_at >= today_start
    ).count()

    s_level = EventCard.query.filter(
        EventCard.created_at >= today_start,
        EventCard.level == "S"
    ).count()

    a_level = EventCard.query.filter(
        EventCard.created_at >= today_start,
        EventCard.level == "A"
    ).count()

    total_sources = NewsSource.query.count()
    active_sources = NewsSource.query.filter_by(is_active=True).count()

    today_articles = RawArticle.query.filter(
        RawArticle.fetched_at >= today_start
    ).count()

    # Latest briefing
    latest_briefing = (
        DailyBriefing.query
        .order_by(DailyBriefing.date.desc())
        .first()
    )
    latest_briefing_date = (
        latest_briefing.date.strftime("%Y-%m-%d") if latest_briefing else None
    )

    # Pipeline status
    if today_events > 0:
        pipeline_status = "正常运行"
    else:
        pipeline_status = "待触发"

    stats = {
        "today_events": today_events,
        "s_level": s_level,
        "a_level": a_level,
        "total_sources": total_sources,
        "active_sources": active_sources,
        "today_articles": today_articles,
        "pipeline_status": pipeline_status,
        "latest_briefing_date": latest_briefing_date,
    }

    # Bubble events — recent events for background animation (last 7 days fallback)
    bubble_cutoff = today_start - timedelta(days=7)
    today_events_all = (
        EventCard.query
        .filter(EventCard.created_at >= bubble_cutoff)
        .order_by(EventCard.created_at.desc())
        .limit(30)
        .all()
    )
    bubble_events = [
        {
            "title": e.title[:50],
            "level": e.level or "B",
            "event_id": e.event_id,
        }
        for e in today_events_all
    ]

    return render_template(
        "home.html",
        stats=stats,
        bubble_events=bubble_events,
    )

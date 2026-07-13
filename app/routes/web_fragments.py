"""Fragment API — returns page content as HTML for SPA navigation."""
import logging
from datetime import date, datetime, timezone, timedelta

from flask import Blueprint, render_template, request, jsonify, abort

from app.extensions import db
from models.card import EventCard
from models.event import Event
from models.source import NewsSource, RawArticle
from models.briefing import DailyBriefing
from models.verification import VerificationResult
from models.scoring import EventScore
from models.market import StockInfo, PriceSnapshot

logger = logging.getLogger(__name__)

bp = Blueprint("web_fragments", __name__, url_prefix="/api/v1/fragment")


def _page(title: str, html: str) -> dict:
    """Return standard fragment response."""
    return {"html": html, "title": title}


# ═══════════════════════════════════════════════════════════════════════
# Home
# ═══════════════════════════════════════════════════════════════════════
@bp.route("/home")
def fragment_home():
    today = date.today()
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

    bubble_cutoff = today_start - timedelta(days=7)
    today_events_all = (
        EventCard.query
        .filter(EventCard.created_at >= bubble_cutoff)
        .order_by(EventCard.created_at.desc())
        .limit(30)
        .all()
    )
    bubble_events = [
        {"title": e.title[:50], "level": e.level or "B", "event_id": e.event_id}
        for e in today_events_all
    ]

    html = render_template("fragments/home.html", bubble_events=bubble_events)
    return _page("BubbleEvent — 投资研究终端", html)


# ═══════════════════════════════════════════════════════════════════════
# Events
# ═══════════════════════════════════════════════════════════════════════
@bp.route("/events")
def fragment_events():
    page = request.args.get("page", 1, type=int)
    level_filter = request.args.get("level", "")

    query = EventCard.query.order_by(EventCard.created_at.desc())

    if level_filter:
        levels = [l.strip() for l in level_filter.split(",") if l.strip()]
        if levels:
            query = query.filter(EventCard.level.in_(levels))

    per_page = 20
    cards = query.paginate(page=page, per_page=per_page, error_out=False)

    html = render_template(
        "fragments/events.html",
        cards=cards.items,
        pagination=cards,
        current_level=level_filter,
    )
    return _page("热点事件 — BubbleEvent", html)


@bp.route("/events/<string:event_id>")
def fragment_event_detail(event_id: str):
    event = Event.query.get(event_id)
    if not event:
        abort(404)

    card = EventCard.query.filter_by(event_id=event_id).first()
    verification = VerificationResult.query.filter_by(event_id=event_id).first()
    score = EventScore.query.filter_by(event_id=event_id).first()

    html = render_template(
        "fragments/event_detail.html",
        event=event,
        card=card,
        verification=verification,
        score=score,
    )
    title = (card.title if card else event.title)[:50] + " — BubbleEvent"
    return _page(title, html)


# ═══════════════════════════════════════════════════════════════════════
# Sources
# ═══════════════════════════════════════════════════════════════════════
@bp.route("/sources")
def fragment_sources():
    sources = NewsSource.query.order_by(
        NewsSource.is_system.desc(),
        NewsSource.created_at.desc()
    ).all()

    total = len(sources)
    active_count = sum(1 for s in sources if s.is_active)
    system_count = sum(1 for s in sources if s.is_system)
    user_count = total - system_count

    html = render_template(
        "fragments/sources.html",
        sources=sources,
        stats={
            "total": total,
            "active": active_count,
            "inactive": total - active_count,
            "system": system_count,
            "user": user_count,
        },
    )
    return _page("信息源管理 — BubbleEvent", html)


# ═══════════════════════════════════════════════════════════════════════
# Prediction
# ═══════════════════════════════════════════════════════════════════════
@bp.route("/prediction")
def fragment_prediction():
    stocks = StockInfo.query.order_by(StockInfo.symbol).limit(100).all()
    snapshots = {p.symbol: p for p in PriceSnapshot.query.all()}
    html = render_template("fragments/prediction.html", stocks=stocks, snapshots=snapshots)
    return _page("走势预测 — BubbleEvent", html)


# ═══════════════════════════════════════════════════════════════════════
# Briefing
# ═══════════════════════════════════════════════════════════════════════
@bp.route("/briefing")
def fragment_briefing():
    target_date_str = request.args.get("date")
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = date.today()
            target_date_str = None  # reset invalid value so template gets a clean date
    else:
        target_date = date.today()

    briefing = DailyBriefing.query.filter_by(date=target_date).first()

    available_dates = [
        b.date.strftime("%Y-%m-%d")
        for b in DailyBriefing.query
            .with_entities(DailyBriefing.date)
            .order_by(DailyBriefing.date.desc())
            .limit(60)
            .all()
    ]

    html = render_template(
        "fragments/briefing.html",
        briefing=briefing,
        target_date=target_date_str or target_date.strftime("%Y-%m-%d"),
        available_dates=available_dates,
    )
    return _page("每日简报 — BubbleEvent", html)


# ═══════════════════════════════════════════════════════════════════════
# Timeline
# ═══════════════════════════════════════════════════════════════════════
@bp.route("/timeline")
def fragment_timeline():
    root_event_id = request.args.get("root")
    root_event = None
    if root_event_id:
        root_event = Event.query.get(root_event_id)

    from agents.timeline_builder import TimelineBuilderAgent
    agent = TimelineBuilderAgent()
    initial_graph = agent.get_graph_data(days=90)

    html = render_template(
        "fragments/timeline.html",
        root_event=root_event,
        root_event_id=root_event_id,
        initial_graph=initial_graph,
    )
    return _page("Bubble — BubbleEvent", html)


# ═══════════════════════════════════════════════════════════════════════
# Assistant
# ═══════════════════════════════════════════════════════════════════════
@bp.route("/assistant")
def fragment_assistant():
    html = render_template("fragments/assistant.html")
    return _page("AI 研究助手 — BubbleEvent", html)

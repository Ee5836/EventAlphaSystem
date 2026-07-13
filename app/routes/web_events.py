"""Event list and detail web views."""
from flask import Blueprint, render_template, request, abort
from models.card import EventCard
from models.event import Event
from models.verification import VerificationResult
from models.scoring import EventScore

bp = Blueprint("web_events", __name__)


@bp.route("/events")
def event_list():
    """Event list page with filtering."""
    page = request.args.get("page", 1, type=int)
    level_filter = request.args.get("level", "")

    query = EventCard.query.order_by(EventCard.created_at.desc())

    if level_filter:
        levels = [l.strip() for l in level_filter.split(",") if l.strip()]
        if levels:
            query = query.filter(EventCard.level.in_(levels))

    per_page = 20
    cards = query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "index.html",
        cards=cards.items,
        pagination=cards,
        current_level=level_filter,
    )


@bp.route("/events/<string:event_id>")
def event_detail(event_id: str):
    """Single event detail page."""
    event = Event.query.get(event_id)
    if not event:
        abort(404)

    card = EventCard.query.filter_by(event_id=event_id).first()
    verification = VerificationResult.query.filter_by(event_id=event_id).first()
    score = EventScore.query.filter_by(event_id=event_id).first()

    return render_template(
        "event_detail.html",
        event=event,
        card=card,
        verification=verification,
        score=score,
    )

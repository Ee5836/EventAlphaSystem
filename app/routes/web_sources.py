"""Source management web views."""
from flask import Blueprint, render_template
from models.source import NewsSource

bp = Blueprint("web_sources", __name__)


@bp.route("/sources")
def source_list():
    """Source management page."""
    sources = NewsSource.query.order_by(
        NewsSource.is_system.desc(),
        NewsSource.created_at.desc()
    ).all()

    # Count stats
    total = len(sources)
    active_count = sum(1 for s in sources if s.is_active)
    system_count = sum(1 for s in sources if s.is_system)
    user_count = total - system_count

    return render_template(
        "sources.html",
        sources=sources,
        stats={
            "total": total,
            "active": active_count,
            "inactive": total - active_count,
            "system": system_count,
            "user": user_count,
        },
    )

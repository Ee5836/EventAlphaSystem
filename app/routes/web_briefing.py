"""Daily briefing web and API routes."""
import logging
from datetime import date, datetime

from flask import Blueprint, render_template, request, jsonify

from models.briefing import DailyBriefing

logger = logging.getLogger(__name__)

web_bp = Blueprint("web_briefing", __name__)
api_bp = Blueprint("api_briefing", __name__, url_prefix="/api/v1/briefing")


# ── Web ─────────────────────────────────────────────────────────────
@web_bp.route("/briefing")
def briefing_page():
    """Render the daily briefing page."""
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

    # Available dates for history picker
    available_dates = [
        b.date.strftime("%Y-%m-%d")
        for b in DailyBriefing.query
            .with_entities(DailyBriefing.date)
            .order_by(DailyBriefing.date.desc())
            .limit(60)
            .all()
    ]

    return render_template(
        "briefing.html",
        briefing=briefing,
        target_date=target_date_str or target_date.strftime("%Y-%m-%d"),
        available_dates=available_dates,
    )


# ── API ─────────────────────────────────────────────────────────────
@api_bp.route("/latest")
def latest_briefing():
    """Get the latest briefing."""
    briefing = (
        DailyBriefing.query
        .order_by(DailyBriefing.date.desc())
        .first()
    )
    if not briefing:
        return jsonify({"success": False, "error": "No briefing found"}), 404

    return jsonify({"success": True, "data": briefing.to_dict()})


@api_bp.route("/<string:date_str>")
def get_briefing(date_str: str):
    """Get briefing for a specific date."""
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"success": False, "error": "Invalid date format, use YYYY-MM-DD"}), 400

    briefing = DailyBriefing.query.filter_by(date=target_date).first()
    if not briefing:
        return jsonify({"success": False, "error": "No briefing for this date"}), 404

    return jsonify({"success": True, "data": briefing.to_dict()})


@api_bp.route("/generate", methods=["POST"])
def trigger_generation():
    """Trigger briefing generation for today."""
    try:
        from agents.daily_briefing import DailyBriefingAgent
        agent = DailyBriefingAgent()

        data = request.get_json(silent=True) or {}
        target_date_str = data.get("date")
        if target_date_str:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        else:
            target_date = date.today()

        # force=True: user explicitly clicked "generate", always regenerate
        briefing = agent.generate(target_date, force=True)
        if not briefing:
            return jsonify({"success": False, "error": "Generation failed"}), 500

        return jsonify({"success": True, "data": briefing.to_dict()})
    except Exception as e:
        logger.error(f"Briefing generation failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/dates")
def list_dates():
    """List all available briefing dates."""
    dates = [
        r[0].strftime("%Y-%m-%d")
        for r in DailyBriefing.query
            .with_entities(DailyBriefing.date)
            .order_by(DailyBriefing.date.desc())
            .limit(90)
            .all()
    ]
    return jsonify({"success": True, "data": dates})

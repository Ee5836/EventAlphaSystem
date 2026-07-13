"""Event REST API."""
import threading
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, abort
from models.card import EventCard
from models.event import Event
from models.verification import VerificationResult
from models.scoring import EventScore
from pipeline.orchestrator import PipelineOrchestrator
from app.utils.serializers import paginated_response

bp = Blueprint("api_events", __name__, url_prefix="/api/v1")

# ── In-memory pipeline run tracker (reset on server restart) ──────────
_pipeline_runs: dict[str, dict] = {}


@bp.route("/events")
def list_events():
    """List events as JSON with filtering and pagination."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    level_filter = request.args.get("level", "")

    query = EventCard.query.order_by(EventCard.created_at.desc())
    if level_filter:
        levels = [l.strip() for l in level_filter.split(",") if l.strip()]
        if levels:
            query = query.filter(EventCard.level.in_(levels))

    return jsonify(**paginated_response(query, page=page, per_page=per_page))


@bp.route("/events/<string:event_id>")
def get_event(event_id: str):
    """Get a single event with full details."""
    event = Event.query.get(event_id)
    if not event:
        abort(404)

    card = EventCard.query.filter_by(event_id=event_id).first()
    verification = VerificationResult.query.filter_by(event_id=event_id).first()
    score = EventScore.query.filter_by(event_id=event_id).first()

    return jsonify({
        "success": True,
        "data": {
            "event": event.to_dict(),
            "card": card.to_dict() if card else None,
            "verification": verification.to_dict() if verification else None,
            "score": score.to_dict() if score else None,
        },
    })


@bp.route("/pipeline/trigger", methods=["POST"])
def trigger_pipeline():
    """Async trigger: start pipeline in background, return run_id immediately.

    Optional JSON body:
        force: bool — skip poll_interval check for scout stage (default: false)

    Returns run_id immediately. Poll GET /api/v1/pipeline/status/<run_id>
    to track progress. Frontend auto-refreshes events when status=completed.
    """
    from flask import current_app

    data = request.get_json(silent=True) or {}
    force = data.get("force", False)

    run_id = str(uuid.uuid4())[:8]
    _pipeline_runs[run_id] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "progress": "starting...",
        "result": None,
    }

    app = current_app._get_current_object()

    def _run_pipeline():
        try:
            with app.app_context():
                orchestrator = PipelineOrchestrator()
                result = orchestrator.run_full_pipeline(
                    force_scout=force, fast_mode=True)
                _pipeline_runs[run_id] = {
                    "status": "completed",
                    "started_at": _pipeline_runs[run_id]["started_at"],
                    "progress": "done",
                    "result": {
                        "success": result.success,
                        "metadata": result.metadata,
                        "errors": result.errors,
                    },
                }
        except Exception as e:
            _pipeline_runs[run_id] = {
                "status": "failed",
                "started_at": _pipeline_runs[run_id]["started_at"],
                "progress": str(e),
                "result": None,
            }

    thread = threading.Thread(target=_run_pipeline, daemon=True)
    thread.start()

    return jsonify({"status": "started", "run_id": run_id})


@bp.route("/pipeline/status/<run_id>")
def pipeline_status(run_id: str):
    """Poll pipeline run status.

    Returns:
        {status: "running"|"completed"|"failed", progress: str, result: {...}}
    """
    run = _pipeline_runs.get(run_id)
    if not run:
        return jsonify({"status": "not_found"}), 404
    return jsonify(run)


@bp.route("/pipeline/trigger/process", methods=["POST"])
def trigger_process_only():
    """Async trigger: run processing pipeline only (skip scout/collection).

    Processes already-collected but unprocessed articles through
    extraction → clustering → verification → scoring → card generation.

    Returns run_id immediately. Poll GET /api/v1/pipeline/status/<run_id>
    to track progress.
    """
    from flask import current_app

    run_id = str(uuid.uuid4())[:8]
    _pipeline_runs[run_id] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "progress": "processing...",
        "result": None,
    }

    app = current_app._get_current_object()

    def _run_process():
        try:
            with app.app_context():
                orchestrator = PipelineOrchestrator()
                result = orchestrator.run_processing_only()
                _pipeline_runs[run_id] = {
                    "status": "completed",
                    "started_at": _pipeline_runs[run_id]["started_at"],
                    "progress": "done",
                    "result": {
                        "success": result.success,
                        "metadata": result.metadata,
                        "errors": result.errors,
                    },
                }
        except Exception as e:
            _pipeline_runs[run_id] = {
                "status": "failed",
                "started_at": _pipeline_runs[run_id]["started_at"],
                "progress": str(e),
                "result": None,
            }

    thread = threading.Thread(target=_run_process, daemon=True)
    thread.start()

    return jsonify({"status": "started", "run_id": run_id})


@bp.route("/pipeline/trigger/scout", methods=["POST"])
def trigger_scout_only():
    """Manually trigger SCOUT ONLY — collect articles without processing.

    Optional JSON body:
        sources: list of source names (default: all active)
        force: bool — skip poll_interval check (default: false)
    """
    from agents.scout import ScoutAgent
    agent = ScoutAgent()

    data = request.get_json(silent=True) or {}
    source_names = data.get("sources", None)
    force = data.get("force", False)
    result = agent.run(source_names=source_names, force=force)

    return jsonify({
        "success": result.success,
        "data": {
            "total_articles": result.metadata.get("total_articles", 0),
            "source_counts": (result.output or {}).get("source_counts", {}),
            "skipped_sources": result.metadata.get("skipped_sources", 0),
            "fetched_sources": result.metadata.get("fetched_sources", 0),
        },
        "errors": result.errors,
    })

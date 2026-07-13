"""Source Management REST API — CRUD, toggle, test connection."""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, abort
from models.source import NewsSource
from app.extensions import db

bp = Blueprint("api_sources", __name__, url_prefix="/api/v1")


# ── list ─────────────────────────────────────────────────────────────
@bp.route("/sources")
def list_sources():
    """List all sources, optionally filtered by active/inactive."""
    active_filter = request.args.get("active")
    query = NewsSource.query.order_by(NewsSource.is_system.desc(), NewsSource.created_at.desc())

    if active_filter is not None:
        query = query.filter_by(is_active=(active_filter.lower() == "true"))

    sources = query.all()
    return jsonify({
        "success": True,
        "data": [s.to_dict() for s in sources],
        "total": len(sources),
    })


# ── get one ──────────────────────────────────────────────────────────
@bp.route("/sources/<string:source_id>")
def get_source(source_id: str):
    """Get a single source by id."""
    source = NewsSource.query.get(source_id)
    if not source:
        abort(404)
    return jsonify({"success": True, "data": source.to_dict()})


# ── create ───────────────────────────────────────────────────────────
@bp.route("/sources", methods=["POST"])
def create_source():
    """Add a new user source."""
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400

    # Check uniqueness
    if NewsSource.query.filter_by(name=name).first():
        return jsonify({"success": False, "error": f"Source '{name}' already exists"}), 409

    display_name = (data.get("display_name") or name).strip()
    base_url = (data.get("base_url") or "").strip()
    source_type = data.get("source_type", "rss")
    if source_type not in ("rss", "webpage", "api"):
        source_type = "rss"

    # Safe type coercion with validation
    try:
        credibility = float(data.get("credibility", 0.5))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "credibility must be a number"}), 400

    try:
        poll_interval = int(data.get("poll_interval", 3600))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "poll_interval must be an integer"}), 400

    source = NewsSource(
        name=name,
        display_name=display_name,
        base_url=base_url,
        source_type=source_type,
        credibility=credibility,
        is_active=data.get("is_active", True),
        is_system=False,
        created_by="user",
        poll_interval=poll_interval,
        tags_json=data.get("tags", []),
        config_json=data.get("config", {}),
    )
    db.session.add(source)
    db.session.commit()

    return jsonify({"success": True, "data": source.to_dict()}), 201


# ── update ───────────────────────────────────────────────────────────
@bp.route("/sources/<string:source_id>", methods=["PUT", "PATCH"])
def update_source(source_id: str):
    """Update an existing source."""
    source = NewsSource.query.get(source_id)
    if not source:
        abort(404)

    # System sources cannot have core fields changed
    data = request.get_json(silent=True) or {}

    if source.is_system:
        # Only allow toggling active and adjusting credibility/poll_interval
        allowed = {"is_active", "credibility", "poll_interval"}
        for k in data:
            if k not in allowed:
                return jsonify({
                    "success": False,
                    "error": f"Cannot modify '{k}' on a system source; only is_active, credibility, poll_interval are allowed"
                }), 403
    else:
        # User sources: full edit
        if "name" in data:
            new_name = data["name"].strip()
            if new_name and new_name != source.name:
                if NewsSource.query.filter_by(name=new_name).first():
                    return jsonify({"success": False, "error": f"Source '{new_name}' already exists"}), 409
                source.name = new_name
        if "display_name" in data:
            source.display_name = data["display_name"].strip()
        if "base_url" in data:
            source.base_url = data["base_url"].strip()
        if "source_type" in data and data["source_type"] in ("rss", "webpage", "api"):
            source.source_type = data["source_type"]
        if "tags" in data:
            source.tags_json = data["tags"]
        if "config" in data:
            source.config_json = data["config"]

    # Common mutable fields
    if "is_active" in data:
        source.is_active = bool(data["is_active"])
    if "credibility" in data:
        try:
            source.credibility = float(data["credibility"])
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "credibility must be a number"}), 400
    if "poll_interval" in data:
        try:
            source.poll_interval = int(data["poll_interval"])
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "poll_interval must be an integer"}), 400

    source.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"success": True, "data": source.to_dict()})


# ── delete ───────────────────────────────────────────────────────────
@bp.route("/sources/<string:source_id>", methods=["DELETE"])
def delete_source(source_id: str):
    """Delete a user source. System sources cannot be deleted."""
    source = NewsSource.query.get(source_id)
    if not source:
        abort(404)

    if source.is_system:
        return jsonify({
            "success": False,
            "error": "System sources cannot be deleted. Use deactivation instead."
        }), 403

    db.session.delete(source)
    db.session.commit()
    return jsonify({"success": True, "deleted": source_id})


# ── toggle ────────────────────────────────────────────────────────────
@bp.route("/sources/<string:source_id>/toggle", methods=["POST"])
def toggle_source(source_id: str):
    """Toggle a source active/inactive."""
    source = NewsSource.query.get(source_id)
    if not source:
        abort(404)

    source.is_active = not source.is_active
    source.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({
        "success": True,
        "data": {
            "id": source.id,
            "is_active": source.is_active,
        },
    })


# ── test connection ──────────────────────────────────────────────────
@bp.route("/sources/test-connection", methods=["POST"])
def test_connection():
    """Test whether a URL is reachable. Does NOT persist anything."""
    import requests as req_lib

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    source_type = data.get("source_type", "rss")

    if not url:
        return jsonify({"success": False, "error": "url is required"}), 400

    result = {
        "url": url,
        "source_type": source_type,
        "reachable": False,
        "status_code": None,
        "sample_size": 0,
        "error": None,
    }

    try:
        resp = req_lib.get(url, timeout=15, headers={
            "User-Agent": "BubbleEvent/1.0 (Investment Research Bot; +https://github.com/bubbleevent)"
        })
        result["reachable"] = 200 <= resp.status_code < 500
        result["status_code"] = resp.status_code
        if result["reachable"]:
            result["sample_size"] = len(resp.text)
    except req_lib.RequestException as e:
        result["error"] = str(e)

    return jsonify({"success": True, "data": result})


# ── collect single source ────────────────────────────────────────────
@bp.route("/sources/<string:source_id>/collect", methods=["POST"])
def collect_source(source_id: str):
    """Manually collect articles from a single source."""
    source = NewsSource.query.get(source_id)
    if not source:
        abort(404)

    if not source.is_active:
        return jsonify({
            "success": False,
            "error": f"Source '{source.name}' is inactive. Enable it first."
        }), 400

    try:
        from agents.scout import ScoutAgent
        agent = ScoutAgent()
        result = agent.run(source_names=[source.name])

        return jsonify({
            "success": result.success,
            "data": {
                "source_name": source.name,
                "articles_collected": result.output.get("source_counts", {}).get(source.name, 0),
                "total_articles": result.metadata.get("total_articles", 0),
            },
            "errors": result.errors,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── batch toggle ─────────────────────────────────────────────────────
@bp.route("/sources/batch-toggle", methods=["POST"])
def batch_toggle():
    """Batch enable/disable sources by id list."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    active = bool(data.get("is_active", True))

    if not ids:
        return jsonify({"success": False, "error": "ids list is required"}), 400

    updated = []
    for sid in ids:
        source = NewsSource.query.get(sid)
        if source:
            source.is_active = active
            source.updated_at = datetime.now(timezone.utc)
            updated.append(sid)

    db.session.commit()
    return jsonify({"success": True, "updated": len(updated), "ids": updated})

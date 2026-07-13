"""Causal timeline web and API routes."""
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, jsonify, abort

from app.extensions import db
from models.timeline import TimelineNode, CausalEdge, TimelineSnapshot
from models.event import Event

logger = logging.getLogger(__name__)

web_bp = Blueprint("web_timeline", __name__)
api_bp = Blueprint("api_timeline", __name__, url_prefix="/api/v1/timeline")


# ── Web ─────────────────────────────────────────────────────────────
@web_bp.route("/timeline")
def timeline_page():
    """Render the causal timeline page."""
    root_event_id = request.args.get("root")
    root_event = None
    if root_event_id:
        root_event = Event.query.get(root_event_id)

    # Pre-fetch graph data so the page renders immediately without a loading spinner
    from agents.timeline_builder import TimelineBuilderAgent
    agent = TimelineBuilderAgent()
    initial_graph = agent.get_graph_data(days=90)

    return render_template(
        "timeline.html",
        root_event=root_event,
        root_event_id=root_event_id,
        initial_graph=initial_graph,
    )


# ── API ─────────────────────────────────────────────────────────────
@api_bp.route("/graph")
def get_graph():
    """Get full graph data for visualization."""
    days = request.args.get("days", 90, type=int)
    include_expired = request.args.get("include_expired", "0") == "1"
    exclude_isolated = request.args.get("exclude_isolated", "1") == "1"

    from agents.timeline_builder import TimelineBuilderAgent
    agent = TimelineBuilderAgent()
    graph = agent.get_graph_data(days=days, include_expired=include_expired, exclude_isolated=exclude_isolated)

    return jsonify({"success": True, "data": graph})


@api_bp.route("/nodes")
def list_nodes():
    """List timeline nodes with optional filters and pagination."""
    node_type = request.args.get("type")
    status = request.args.get("status")
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    include_expired = request.args.get("include_expired", "0") == "1"

    query = TimelineNode.query.order_by(TimelineNode.timestamp.desc())

    if not include_expired:
        query = query.filter(
            db.or_(
                TimelineNode.expires_at.is_(None),
                TimelineNode.expires_at >= datetime.now(timezone.utc),
            )
        )

    if node_type:
        query = query.filter_by(node_type=node_type)
    if status:
        query = query.filter_by(status=status)

    nodes = query.offset(offset).limit(limit).all()

    return jsonify({
        "success": True,
        "data": [n.to_dict() for n in nodes],
    })


@api_bp.route("/nodes/<string:node_id>")
def get_node(node_id: str):
    """Get a single node with its edges."""
    node = TimelineNode.query.get(node_id)
    if not node:
        abort(404)

    # Get downstream predictions (distinct: avoid duplicates from multiple edges)
    downstream = (
        TimelineNode.query
        .join(CausalEdge, CausalEdge.target_node_id == TimelineNode.id)
        .filter(CausalEdge.source_node_id == node_id)
        .distinct()
        .all()
    )

    # Get upstream causes (distinct: avoid duplicates from multiple edges)
    upstream = (
        TimelineNode.query
        .join(CausalEdge, CausalEdge.source_node_id == TimelineNode.id)
        .filter(CausalEdge.target_node_id == node_id)
        .distinct()
        .all()
    )

    return jsonify({
        "success": True,
        "data": {
            "node": node.to_dict(),
            "upstream": [{"id": n.id, "title": n.title, "node_type": n.node_type, "status": n.status} for n in upstream],
            "downstream": [{"id": n.id, "title": n.title, "node_type": n.node_type, "status": n.status} for n in downstream],
        },
    })


@api_bp.route("/edges/<string:node_id>")
def get_edges(node_id: str):
    """Get all edges connected to a node."""
    outgoing = CausalEdge.query.filter_by(source_node_id=node_id).all()
    incoming = CausalEdge.query.filter_by(target_node_id=node_id).all()

    return jsonify({
        "success": True,
        "data": {
            "outgoing": [e.to_dict() for e in outgoing],
            "incoming": [e.to_dict() for e in incoming],
        },
    })


@api_bp.route("/discover", methods=["POST"])
def trigger_discovery():
    """Trigger causal discovery for an event."""
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")

    if not event_id:
        return jsonify({"success": False, "error": "event_id is required"}), 400

    try:
        from agents.timeline_builder import TimelineBuilderAgent
        agent = TimelineBuilderAgent()

        # Add event as node
        node = agent.add_event_node(event_id)
        if not node:
            return jsonify({"success": False, "error": "Event not found"}), 404

        # Discover causal links
        edges = agent.discover_causal_links(node.id)

        # Extend predictions
        predictions = agent.extend_predictions(node.id)

        return jsonify({
            "success": True,
            "data": {
                "node_id": node.id,
                "edges_found": len(edges),
                "predictions": len(predictions),
            },
        })
    except Exception as e:
        logger.error(f"Causal discovery failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/edges/<string:edge_id>/verify", methods=["POST"])
def verify_edge(edge_id: str):
    """Set verification status of an edge.

    Body: {"verified": true|false|null}
      true  → confirmed causal link
      false → refuted (not causal)
      null  → reset to inferred (unverified)
    """
    edge = CausalEdge.query.get(edge_id)
    if not edge:
        abort(404)

    data = request.get_json(silent=True) or {}
    new_status = data.get("verified")  # True / False / None

    if new_status is not None and not isinstance(new_status, bool):
        try:
            new_status = {"true": True, "false": False, "null": None}[str(new_status).lower()]
        except KeyError:
            return jsonify({"success": False, "error": "verified must be true, false, or null"}), 400

    edge.verified = new_status
    db.session.commit()

    status_label = {True: "已确认因果", False: "已证伪", None: "推断关联"}
    return jsonify({
        "success": True,
        "data": {
            "edge_id": edge.id,
            "verified": edge.verified,
            "status_label": status_label.get(edge.verified, "未知"),
        },
    })


@api_bp.route("/extend/<string:node_id>", methods=["POST"])
def extend_node(node_id: str):
    """Extend predictions from a timeline node."""
    try:
        from agents.timeline_builder import TimelineBuilderAgent
        agent = TimelineBuilderAgent()
        predictions = agent.extend_predictions(node_id)
        return jsonify({
            "success": True,
            "data": {
                "predictions": len(predictions),
                "prediction_ids": [p.id for p in predictions],
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/rebuild", methods=["POST"])
def rebuild_timeline():
    """Full rebuild: delete all nodes + edges, then rebuild from EventCards.

    S-level cards are prioritized to ensure root_event nodes are created.
    This is useful when the initial auto-build missed S-level events.
    """
    try:
        from agents.timeline_builder import TimelineBuilderAgent
        agent = TimelineBuilderAgent()
        result = agent.rebuild()
        return jsonify({
            "success": True,
            "data": result,
        })
    except Exception as e:
        logger.error(f"Rebuild failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/isolated")
def list_isolated():
    """List isolated timeline nodes (nodes with no causal edges).

    Query params:
        type:     filter by node_type (root_event/derived_event/etc.)
        search:   search in title
        level:    filter by metadata level (S/A/B)
        limit:    max results (default 200)
        offset:   pagination offset
        include_expired: include expired nodes (default 0)
    """
    node_type = request.args.get("type")
    search = request.args.get("search")
    level = request.args.get("level")
    limit = request.args.get("limit", 200, type=int)
    offset = request.args.get("offset", 0, type=int)
    include_expired = request.args.get("include_expired", "0") == "1"

    # Collect all node IDs that appear in any CausalEdge
    connected_ids: set = set()
    edge_rows = CausalEdge.query.with_entities(
        CausalEdge.source_node_id, CausalEdge.target_node_id
    ).all()
    for row in edge_rows:
        connected_ids.add(row[0])
        connected_ids.add(row[1])

    # Build query for isolated nodes (nodes with no causal edges)
    if connected_ids:
        query = TimelineNode.query.filter(~TimelineNode.id.in_(connected_ids))
    else:
        query = TimelineNode.query  # all nodes are isolated (no edges at all)

    if node_type:
        query = query.filter_by(node_type=node_type)
    if search:
        query = query.filter(TimelineNode.title.contains(search))
    if not include_expired:
        query = query.filter(
            db.or_(
                TimelineNode.expires_at.is_(None),
                TimelineNode.expires_at >= datetime.now(timezone.utc),
            )
        )

    total = query.count()
    nodes = query.order_by(TimelineNode.confidence.desc()).offset(offset).limit(limit).all()

    # Count by type for filter chips
    type_counts = {}
    from sqlalchemy import func
    if connected_ids:
        count_query = db.session.query(
            TimelineNode.node_type, func.count(TimelineNode.id)
        ).filter(~TimelineNode.id.in_(connected_ids))
    else:
        count_query = db.session.query(
            TimelineNode.node_type, func.count(TimelineNode.id)
        )
    count_query = count_query.group_by(TimelineNode.node_type)
    for t, c in count_query.all():
        type_counts[t] = c

    # Filter by metadata level if specified
    result_nodes = []
    for n in nodes:
        meta = n.metadata_json or {}
        node_level = meta.get("level", "")
        if level and node_level != level:
            continue
        d = n.to_dict()
        d["level"] = node_level
        result_nodes.append(d)

    return jsonify({
        "success": True,
        "data": {
            "nodes": result_nodes,
            "total": total,
            "type_counts": type_counts,
        },
    })


@api_bp.route("/cleanup", methods=["POST"])
def cleanup_expired():
    """Hard-delete expired timeline nodes.

    Body (optional): {"older_than_days": 30}
    """
    data = request.get_json(silent=True) or {}
    older_than_days = data.get("older_than_days", 30)

    try:
        from agents.timeline_builder import TimelineBuilderAgent
        agent = TimelineBuilderAgent()
        result = agent.cleanup_expired_nodes(older_than_days=older_than_days)
        return jsonify({
            "success": True,
            "data": result,
        })
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/nodes/<string:node_id>", methods=["DELETE"])
def delete_node(node_id: str):
    """Delete a single timeline node. Associated edges are cascade-deleted by the DB."""
    node = TimelineNode.query.get(node_id)
    if not node:
        abort(404)
    try:
        # Manually delete associated edges first — SQLite FK PRAGMA may not be enabled
        CausalEdge.query.filter(
            (CausalEdge.source_node_id == node_id) | (CausalEdge.target_node_id == node_id)
        ).delete(synchronize_session="fetch")
        db.session.delete(node)
        db.session.commit()
        return jsonify({"success": True, "deleted": node_id})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to delete node {node_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/snapshots")
def list_snapshots():
    """List timeline snapshots."""
    snapshots = (
        TimelineSnapshot.query
        .order_by(TimelineSnapshot.date.desc())
        .limit(30)
        .all()
    )
    return jsonify({
        "success": True,
        "data": [s.to_dict() for s in snapshots],
    })


@api_bp.route("/snapshot", methods=["POST"])
def take_snapshot():
    """Take a new timeline snapshot."""
    try:
        from agents.timeline_builder import TimelineBuilderAgent
        agent = TimelineBuilderAgent()
        snapshot = agent.take_snapshot()
        return jsonify({
            "success": True,
            "data": snapshot.to_dict(),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

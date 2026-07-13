"""Timeline Builder Agent — builds causal event networks."""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.extensions import db
from models.timeline import TimelineNode, CausalEdge, TimelineSnapshot
from models.event import Event
from llm.factory import get_llm
from agents.edge_strength import (
    compute_edge_strength,
    build_edge_context,
    encode_nodes_batch,
    semantic_similarity,
    TagStatistics,
)

logger = logging.getLogger(__name__)

# ── Impact periods (days) — determines how long a timeline node remains visible ──
IMPACT_PERIODS = {
    # By event level (from EventCard)
    "S": 90,       # systemic/macro events — long-lasting structural impact
    "A": 30,       # significant events — shape market sentiment for weeks
    "B": 14,       # moderate events — short-term relevance
    "C": 7,        # minor events — quick noise

    # By prediction time horizon
    "prediction": {
        "T+3": 3,
        "T+7": 7,
        "T+30": 30,
    },

    # By node type (fallback when level is not available)
    "market_reaction": 7,
    "verification": 30,
    "root_event": 30,       # add_event_node() has no level info
    "derived_event": 14,    # typically B-level events

    # Ultimate fallback
    "DEFAULT": 30,
}


class TimelineBuilderAgent:
    """Builds and maintains causal event timelines.

    Workflow:
    1. Event intake — new events become timeline nodes
    2. Causal discovery — LLM + vector search finds causal links
    3. Prediction extension — predict downstream effects
    4. Verification — T+N days later, verify predictions
    5. Growth — timeline accumulates and learns patterns
    """

    def __init__(self):
        pass

    # ── Node creation ──────────────────────────────────────────────
    def add_event_node(self, event_id: str) -> Optional[TimelineNode]:
        """Add an event as a timeline node."""
        event = Event.query.get(event_id)
        if not event:
            return None

        # Check if node already exists
        existing = TimelineNode.query.filter_by(event_id=event_id).first()
        if existing:
            return existing

        # Determine impact period from EventCard level, or fall back to default
        from models.card import EventCard
        card = EventCard.query.filter_by(event_id=event_id).first()
        if card and card.level:
            days = IMPACT_PERIODS.get(card.level, IMPACT_PERIODS["DEFAULT"])
        else:
            days = IMPACT_PERIODS["root_event"]

        base_time = event.created_at or datetime.now(timezone.utc)
        expires_at = base_time + timedelta(days=days)

        ev_type = event.event_type or "未知"
        ev_cat = event.event_category or "未分类"
        node = TimelineNode(
            node_type="root_event",
            event_id=event_id,
            title=event.title or "Untitled",
            description=f"Type: {ev_type}, Category: {ev_cat}",
            timestamp=base_time,
            status="confirmed",
            confidence=event.confidence or 0.8,
            tags_json=event.affected_industries_json or [],
            metadata_json={
                "event_type": event.event_type,
                "event_category": event.event_category,
                "entities": event.entities_json,
                "location": event.location,
            },
            expires_at=expires_at,
        )
        db.session.add(node)
        db.session.commit()
        logger.info(f"Added timeline node for event {event_id}, expires_at={expires_at.isoformat()}")
        return node

    # ── Causal discovery ───────────────────────────────────────────
    def discover_causal_links(self, node_id: str) -> list[CausalEdge]:
        """Discover causal links between a node and existing timeline nodes."""
        node = TimelineNode.query.get(node_id)
        if not node:
            return []

        # Get candidate nodes (same tags or recent)
        candidates = (
            TimelineNode.query
            .filter(TimelineNode.id != node_id)
            .order_by(TimelineNode.timestamp.desc())
            .limit(30)
            .all()
        )

        if not candidates:
            return []

        # Use LLM to find causal relationships
        edges = self._llm_discover_links(node, candidates)

        # Save new edges with multi-dimensional strength blending
        saved = []
        if edges:
            # Pre-compute embeddings for the source node and all candidates
            all_involved = [node] + candidates
            emb_map = encode_nodes_batch(all_involved)
            tags_src = set(node.tags_json or [])
            level_src = (node.metadata_json or {}).get("level") if hasattr(node, "metadata_json") else None

            # Build lightweight tag stats from candidates + node
            tag_stats = TagStatistics(all_involved)

            for edge_data in edges:
                target_id = edge_data.get("target")
                target_node = next((c for c in candidates if c.id == target_id), None)

                llm_strength = edge_data.get("strength", 0.5)
                relation_type = edge_data.get("relation_type", "influences")

                # Blend with formula if target node found
                if target_node:
                    tags_tgt = set(target_node.tags_json or [])
                    level_tgt = (target_node.metadata_json or {}).get("level") if hasattr(target_node, "metadata_json") else None
                    formula_strength = compute_edge_strength(
                        emb_a=emb_map.get(node.id),
                        emb_b=emb_map.get(target_id),
                        tags_a=tags_src,
                        tags_b=tags_tgt,
                        level_a=level_src,
                        level_b=level_tgt,
                        timestamp_a=node.timestamp,
                        timestamp_b=target_node.timestamp,
                        relation_type=relation_type,
                        tag_stats=tag_stats,
                    )
                    final_strength = round(0.6 * llm_strength + 0.4 * formula_strength, 4)
                else:
                    final_strength = llm_strength

                edge = CausalEdge(
                    source_node_id=edge_data["source"],
                    target_node_id=edge_data["target"],
                    relation_type=relation_type,
                    strength=final_strength,
                    logic_chain=edge_data.get("logic_chain", ""),
                    created_by="llm",
                )
                db.session.add(edge)
                saved.append(edge)

        if saved:
            db.session.commit()
            logger.info(f"Discovered {len(saved)} causal edges for node {node_id} (blended strength)")

        return saved

    def _llm_discover_links(
        self, node: TimelineNode, candidates: list[TimelineNode]
    ) -> list[dict]:
        """Use LLM to identify causal relationships."""
        try:
            llm = get_llm()

            import json
            node_info = {
                "title": node.title,
                "description": node.description,
                "tags": node.tags_json,
                "timestamp": node.timestamp.isoformat() if node.timestamp else "",
            }
            candidates_info = [
                {
                    "id": c.id,
                    "title": c.title,
                    "description": c.description[:200],
                    "tags": c.tags_json,
                    "timestamp": c.timestamp.isoformat() if c.timestamp else "",
                }
                for c in candidates[:10]
            ]

            prompt = f"""分析以下事件节点之间的关系，识别因果联系。

当前事件:
{json.dumps(node_info, ensure_ascii=False, indent=2)}

候选相关事件:
{json.dumps(candidates_info, ensure_ascii=False, indent=2)}

请返回JSON数组，每个条目描述一个因果关系:
[
  {{
    "source": "源节点id（原因方）",
    "target": "目标节点id（结果方）",
    "relation_type": "causes|influences|correlates|contradicts",
    "strength": 0.0-1.0,
    "logic_chain": "推理链条（50字内）"
  }}
]

strength评分参考：
- 0.25-0.40: 弱关联（同行业但无明确因果链）
- 0.40-0.60: 中等关联（有逻辑传导但距离较远）
- 0.60-0.80: 强关联（直接因果，时间顺序清晰）
- 0.80-1.0: 极强关联（政策直接引发市场反应等）

规则：
- 只返回确实存在因果关系的链接（strength >= 0.25）
- 因果关系方向：原因在前，结果在后
- 时间顺序必须合理
- 最多返回5条"""

            result = llm.complete_json(
                "你是一个事件因果分析专家。只返回JSON数组。",
                prompt,
                max_tokens=1000,
                temperature=0.1,
            )

            if isinstance(result, list):
                return result
            return []

        except Exception as e:
            logger.error(f"LLM causal discovery failed: {e}")
            return []

    # ── Prediction extension ────────────────────────────────────────
    def extend_predictions(self, node_id: str, max_predictions: int = 5) -> list[TimelineNode]:
        """Generate prediction nodes from a timeline node.

        Returns list of created prediction TimelineNode objects.
        """
        node = TimelineNode.query.get(node_id)
        if not node:
            return []

        savepoint = db.session.begin_nested()
        try:
            llm = get_llm()
            import json

            prompt = f"""基于以下事件，预测可能的连锁反应。

事件: {node.title}
描述: {node.description or ''}
标签: {json.dumps(node.tags_json or [], ensure_ascii=False)}

请返回JSON数组，列出3-5个可能的后续发展:
[
  {{
    "title": "预测标题（15字内）",
    "description": "详细描述（80字内）",
    "confidence": 0.0-1.0,
    "time_horizon": "T+3|T+7|T+30",
    "tags": ["相关标签"]
  }}
]

⚠️ 预测仅供参考，不构成投资建议。"""

            predictions = llm.complete_json(
                "你是一个宏观经济与行业分析专家。只返回JSON数组。",
                prompt,
                max_tokens=800,
                temperature=0.4,
            )

            if not isinstance(predictions, list):
                return []

            # Pre-compute embedding for the source node (reused for all predictions)
            src_emb_map = encode_nodes_batch([node])
            src_emb = src_emb_map.get(node.id)

            level_src = (node.metadata_json or {}).get("level") if hasattr(node, "metadata_json") else None
            tags_src = set(node.tags_json or [])

            nodes = []
            for pred in predictions[:max_predictions]:
                # Compute expiration from prediction time horizon
                time_horizon = pred.get("time_horizon", "T+7")
                prediction_days = IMPACT_PERIODS["prediction"].get(time_horizon, 7)
                pred_expires_at = datetime.now(timezone.utc) + timedelta(days=prediction_days)

                pred_node = TimelineNode(
                    node_type="prediction",
                    title=pred.get("title", "Prediction"),
                    description=pred.get("description", ""),
                    timestamp=datetime.now(timezone.utc),
                    status="predicted",
                    confidence=pred.get("confidence", 0.5),
                    tags_json=pred.get("tags", []),
                    metadata_json={
                        "source_node_id": node_id,
                        "time_horizon": pred.get("time_horizon", "T+7"),
                    },
                    expires_at=pred_expires_at,
                )
                db.session.add(pred_node)
                db.session.flush()

                # ── Compute multi-dimensional edge strength ──
                pred_tags = set(pred.get("tags", []))
                pred_confidence = pred.get("confidence", 0.5)

                # Embed the prediction title for semantic similarity
                pred_emb_map = encode_nodes_batch([pred_node])
                pred_emb = pred_emb_map.get(pred_node.id)

                formula_strength = compute_edge_strength(
                    emb_a=src_emb,
                    emb_b=pred_emb,
                    tags_a=tags_src,
                    tags_b=pred_tags,
                    level_a=level_src,
                    level_b=None,  # prediction has no event level
                    timestamp_a=node.timestamp,
                    timestamp_b=pred_node.timestamp,
                    relation_type="causes",
                    tag_stats=None,  # no tag stats for predictions (new tags)
                )

                # Blend: 50% LLM confidence + 50% formula
                final_strength = round(0.5 * pred_confidence + 0.5 * formula_strength, 4)

                edge = CausalEdge(
                    source_node_id=node_id,
                    target_node_id=pred_node.id,
                    relation_type="causes",
                    strength=final_strength,
                    logic_chain=f"预测: {node.title} → {pred.get('title', '')}",
                    created_by="llm",
                )
                db.session.add(edge)
                nodes.append(pred_node)

            savepoint.commit()
            logger.info(f"Extended {len(nodes)} predictions from node {node_id} (multi-dim strength)")
            return nodes

        except Exception as e:
            logger.error(f"Prediction extension failed: {e}")
            savepoint.rollback()
            return []

    # ── Auto-build from events ──────────────────────────────────────
    def auto_build_from_events(self, max_events: int = 100, force: bool = False) -> int:
        """Create timeline nodes from existing EventCards.

        Args:
            max_events: Maximum number of EventCards to process.
            force: If True, rebuild even if nodes already exist (incremental —
                   only creates nodes for EventCards that don't have one yet).

        Returns number of nodes created.
        """
        from models.card import EventCard

        # Only skip if NOT forcing and nodes already exist
        if not force:
            existing_count = TimelineNode.query.count()
            if existing_count > 0:
                return 0

        # Prioritize S-level cards first, then A, then B — ensures root_event nodes get created
        cards_s = (
            EventCard.query
            .filter(EventCard.level == "S")
            .order_by(EventCard.created_at.desc())
            .limit(max_events)
            .all()
        )
        cards_a = (
            EventCard.query
            .filter(EventCard.level == "A")
            .order_by(EventCard.created_at.desc())
            .limit(max_events)
            .all()
        )
        cards_b = (
            EventCard.query
            .filter(EventCard.level == "B")
            .order_by(EventCard.created_at.desc())
            .limit(max_events // 2)
            .all()
        )

        # Merge: S first, then A, then B — up to max_events total each
        cards = cards_s + cards_a + cards_b

        if not cards:
            # Fallback to all cards
            cards = (
                EventCard.query
                .order_by(EventCard.created_at.desc())
                .limit(max_events)
                .all()
            )

        if not cards:
            return 0

        logger.info(f"Auto-building timeline from {len(cards)} event cards...")

        # Create nodes for each card
        created = 0
        for card in cards:
            try:
                existing = TimelineNode.query.filter_by(event_id=card.event_id).first()
                if existing:
                    continue

                level = card.level or "B"
                # Compute expiration from event level
                level_days = IMPACT_PERIODS.get(level, IMPACT_PERIODS["DEFAULT"])
                expires_at = (card.created_at or datetime.now(timezone.utc)) + timedelta(days=level_days)

                # Diversify node types — check event content FIRST, then level
                ev_type = (card.event_type or "").lower()
                if any(kw in ev_type for kw in ["市场", "行情", "market", "price", "commodity", "currency", "trading"]):
                    node_type = "market_reaction"
                elif level == "S":
                    node_type = "root_event"
                elif level == "A":
                    node_type = "root_event" if "政策" in ev_type or "宏观" in ev_type else "derived_event"
                else:
                    node_type = "derived_event"

                node = TimelineNode(
                    node_type=node_type,
                    event_id=card.event_id,
                    title=card.title or "Untitled",
                    description=(card.summary or "")[:500],
                    timestamp=card.created_at or datetime.now(timezone.utc),
                    status="confirmed" if card.credibility and card.credibility >= 0.7 else "pending",
                    confidence=card.credibility or 0.6,
                    tags_json=card.affected_industries or [],
                    metadata_json={
                        "level": card.level,
                        "credibility_label": card.credibility_label,
                        "event_type": card.event_type,
                    },
                    expires_at=expires_at,
                )
                db.session.add(node)
                created += 1
            except Exception as e:
                logger.warning(f"Failed to create timeline node for {card.event_id}: {e}")
                continue

        if created > 0:
            db.session.commit()
            logger.info(f"Auto-created {created} timeline nodes from event cards")

            # Discover causal links between the newly created nodes
            self._auto_discover_links()

        return created

    def rebuild(self) -> dict:
        """Full rebuild: delete all nodes and edges, then rebuild from EventCards.

        Returns dict with rebuild statistics.
        """
        # Delete edges first (FK constraint)
        edge_count = CausalEdge.query.count()
        if edge_count > 0:
            CausalEdge.query.delete()
            logger.info(f"Deleted {edge_count} causal edges")

        # Delete all nodes
        node_count = TimelineNode.query.count()
        if node_count > 0:
            TimelineNode.query.delete()
            logger.info(f"Deleted {node_count} timeline nodes")

        db.session.commit()
        logger.info("Timeline cleared — rebuilding from EventCards...")

        # Rebuild — force=true bypasses the empty-check guard
        created = self.auto_build_from_events(max_events=200, force=True)

        # Run causal discovery
        edge_created = self._auto_discover_links()

        # Generate predictions for top nodes
        pred_created = 0
        try:
            top_nodes = (
                TimelineNode.query
                .filter(TimelineNode.node_type.in_(["root_event", "derived_event"]))
                .order_by(TimelineNode.confidence.desc())
                .limit(5)
                .all()
            )
            for node in top_nodes:
                try:
                    preds = self.extend_predictions(node.id, max_predictions=2)
                    pred_created += len(preds)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Prediction generation during rebuild skipped: {e}")

        db.session.commit()

        result = {
            "nodes_deleted": node_count,
            "edges_deleted": edge_count,
            "nodes_created": created,
            "edges_created": edge_created,
            "predictions_created": pred_created,
        }
        logger.info(f"Rebuild complete: {result}")
        return result

    def _auto_discover_links(self) -> int:
        """Discover causal links among timeline nodes.

        Uses two strategies:
        1. Rule-based: multi-dimensional edge strength (semantic + NPMI +
           Jaccard + temporal decay + level weighting) — fast, statistically grounded
        2. LLM-based: semantic causal analysis (slower but higher quality)
        """
        all_nodes = (
            TimelineNode.query
            .order_by(TimelineNode.timestamp.asc())
            .limit(50)
            .all()
        )

        if len(all_nodes) < 2:
            return 0

        # Check if edges already exist
        existing_edges = CausalEdge.query.count()
        if existing_edges > 0:
            return 0

        logger.info(f"Discovering causal links among {len(all_nodes)} nodes...")

        total_edges = 0

        # ══ Pre-compute shared context for batch edge strength ══
        ctx = build_edge_context(all_nodes)
        embeddings = ctx["embeddings"]
        tag_stats: TagStatistics = ctx["tag_stats"]

        # ══ Strategy 1: Multi-dimensional edge strength ══
        # Link node pairs that pass a minimum relevance threshold
        MIN_STRENGTH_THRESHOLD = 0.15  # below this, don't create an edge at all

        for i, node_a in enumerate(all_nodes):
            tags_a = set(node_a.tags_json or [])
            if not tags_a:
                continue
            for node_b in all_nodes[i + 1:]:
                tags_b = set(node_b.tags_json or [])
                common = tags_a & tags_b

                # Require at least 1 shared tag OR compute semantic sim as fallback
                if not common:
                    continue

                # Determine direction by timestamp
                t_a = node_a.timestamp
                t_b = node_b.timestamp
                if t_a and t_b:
                    if t_a <= t_b:
                        source, target = node_a, node_b
                    else:
                        source, target = node_b, node_a
                else:
                    source, target = node_a, node_b

                # Avoid duplicate edges
                dup = CausalEdge.query.filter_by(
                    source_node_id=source.id, target_node_id=target.id
                ).first()
                if dup:
                    continue

                # ── Multi-dimensional edge strength ──
                level_a = (source.metadata_json or {}).get("level") if hasattr(source, "metadata_json") else None
                level_b = (target.metadata_json or {}).get("level") if hasattr(target, "metadata_json") else None

                strength = compute_edge_strength(
                    emb_a=embeddings.get(source.id),
                    emb_b=embeddings.get(target.id),
                    tags_a=tags_a,
                    tags_b=tags_b,
                    level_a=level_a,
                    level_b=level_b,
                    timestamp_a=source.timestamp,
                    timestamp_b=target.timestamp,
                    relation_type="correlates",
                    tag_stats=tag_stats,
                )

                if strength < MIN_STRENGTH_THRESHOLD:
                    continue

                # Auto-verify: high strength + high mutual confidence + 2+ shared tags
                auto_verify = (
                    strength >= 0.55 and
                    len(common) >= 2 and
                    (source.confidence or 0) >= 0.7 and
                    (target.confidence or 0) >= 0.7
                )

                # Build informative logic_chain from the dimensions
                logic_parts = [f"共享标签: {', '.join(sorted(common)[:3])}"]
                if embeddings.get(source.id) is not None and embeddings.get(target.id) is not None:
                    sem = semantic_similarity(embeddings[source.id], embeddings[target.id])
                    logic_parts.append(f"语义相似度: {sem:.2f}")
                logic_parts.append(f"强度: {strength:.2f}")

                edge = CausalEdge(
                    source_node_id=source.id,
                    target_node_id=target.id,
                    relation_type="correlates",
                    strength=strength,
                    logic_chain=" | ".join(logic_parts),
                    verified=True if auto_verify else None,
                    created_by="rule_based",
                )
                db.session.add(edge)
                total_edges += 1

        if total_edges > 0:
            db.session.commit()
            logger.info(f"Rule-based: created {total_edges} multi-dimensional edges")

        # ══ Strategy 2: LLM semantic causal analysis (concurrent) ══
        try:
            from utils.concurrent import run_concurrently
            import json

            batch_size = 10
            seen_edge_keys = set()

            # Build batch items for concurrent execution
            llm_batches = []
            for i in range(0, len(all_nodes), batch_size):
                batch = all_nodes[i:i + batch_size]
                if len(batch) < 2:
                    continue
                nodes_info = [
                    {
                        "id": n.id,
                        "title": n.title,
                        "tags": n.tags_json,
                        "timestamp": n.timestamp.isoformat() if n.timestamp else "",
                    }
                    for n in batch
                ]
                llm_batches.append({
                    "batch_nodes": nodes_info,
                    "node_map": {n.id: n for n in batch},
                })

            if not llm_batches:
                return total_edges

            prompt_template = """分析以下事件之间的因果关系，找出3-5对因果链。

{nodes_json}

返回JSON数组（只返回有明确因果联系的对，strength>=0.4）：
[{{"source":"原因id","target":"结果id","relation_type":"causes|influences","strength":0.4-1.0,"logic_chain":"简短推理(20字)"}}]

strength评分参考：
- 0.4-0.5: 弱关联（同行业但无明确因果）
- 0.5-0.7: 中等关联（有逻辑传导但距离较远）
- 0.7-0.85: 强关联（直接因果，时间顺序清晰）
- 0.85-1.0: 极强关联（政策直接引发市场反应等）"""

            def _analyze_batch(item: dict):  # -> list of edge dicts
                """Worker: LLM causal analysis for one batch of nodes."""
                llm = get_llm()
                nodes_json = json.dumps(item["batch_nodes"], ensure_ascii=False, indent=2)
                prompt = prompt_template.format(nodes_json=nodes_json)
                try:
                    result = llm.complete_json(
                        "你是事件因果分析专家。只返回JSON数组。",
                        prompt,
                        max_tokens=800,
                        temperature=0.1,
                    )
                    if isinstance(result, list):
                        return result
                except Exception as e:
                    logger.warning(f"LLM causal analysis batch failed: {e}")
                return []

            successes, _ = run_concurrently(
                items=llm_batches,
                worker_fn=_analyze_batch,
                max_workers=4,  # conservative for causal analysis LLM calls
                description="causal_discovery",
            )

            # ── Main-thread reconciliation ──
            llm_edges = 0
            for edges_list in successes:
                if not edges_list:
                    continue
                for edge_data in edges_list:
                    try:
                        source_id = edge_data.get("source")
                        target_id = edge_data.get("target")
                        if not source_id or not target_id:
                            continue
                        edge_key = (source_id, target_id)
                        if edge_key in seen_edge_keys:
                            continue
                        dup = CausalEdge.query.filter_by(
                            source_node_id=source_id,
                            target_node_id=target_id,
                        ).first()
                        if dup:
                            seen_edge_keys.add(edge_key)
                            continue

                        seen_edge_keys.add(edge_key)

                        # ── Blend LLM strength with multi-dimensional formula ──
                        llm_strength = edge_data.get("strength", 0.5)
                        relation_type = edge_data.get("relation_type", "influences")

                        # Find node objects from all_nodes
                        source_node = next((n for n in all_nodes if n.id == source_id), None)
                        target_node = next((n for n in all_nodes if n.id == target_id), None)

                        formula_strength = None
                        if source_node and target_node:
                            tags_s = set(source_node.tags_json or [])
                            tags_t = set(target_node.tags_json or [])
                            level_s = (source_node.metadata_json or {}).get("level") if hasattr(source_node, "metadata_json") else None
                            level_t = (target_node.metadata_json or {}).get("level") if hasattr(target_node, "metadata_json") else None
                            formula_strength = compute_edge_strength(
                                emb_a=embeddings.get(source_id),
                                emb_b=embeddings.get(target_id),
                                tags_a=tags_s,
                                tags_b=tags_t,
                                level_a=level_s,
                                level_b=level_t,
                                timestamp_a=source_node.timestamp,
                                timestamp_b=target_node.timestamp,
                                relation_type=relation_type,
                                tag_stats=tag_stats,
                            )

                        # Blend: 60% LLM judgment + 40% formula (if available)
                        if formula_strength is not None:
                            final_strength = round(0.6 * llm_strength + 0.4 * formula_strength, 4)
                        else:
                            final_strength = llm_strength

                        edge = CausalEdge(
                            source_node_id=source_id,
                            target_node_id=target_id,
                            relation_type=relation_type,
                            strength=final_strength,
                            logic_chain=edge_data.get("logic_chain", ""),
                            verified=True if final_strength >= 0.65 else None,
                            created_by="llm_auto",
                        )
                        db.session.add(edge)
                        llm_edges += 1
                        total_edges += 1
                    except Exception:
                        continue

            if llm_edges > 0:
                db.session.commit()
                logger.info(f"LLM-based: created {llm_edges} causal edges (blended strength)")

        except Exception as e:
            logger.warning(f"LLM causal discovery skipped (non-blocking): {e}")
            db.session.rollback()

        # ══ Create verification nodes for auto-verified edges ══
        try:
            verified_edges = (
                CausalEdge.query
                .filter_by(verified=True)
                .order_by(CausalEdge.created_at.desc())
                .limit(5).all()
            )
            verif_created = 0
            for edge in verified_edges:
                if verif_created >= 2:  # Max 2 verification nodes per build
                    break
                source = TimelineNode.query.get(edge.source_node_id)
                target = TimelineNode.query.get(edge.target_node_id)
                if not source or not target:
                    continue

                v_node = TimelineNode(
                    node_type="verification",
                    title=f"已验证因果: {source.title[:15]} → {target.title[:15]}",
                    description=f"系统自动验证: {edge.logic_chain or '因果关联'} (via {edge.created_by})",
                    timestamp=datetime.now(timezone.utc),
                    status="confirmed",
                    confidence=edge.strength or 0.7,
                    tags_json=[],
                    metadata_json={
                        "edge_id": edge.id,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                        "verified_by": edge.created_by,
                    },
                    expires_at=datetime.now(timezone.utc) + timedelta(days=IMPACT_PERIODS["verification"]),
                )
                db.session.add(v_node)
                verif_created += 1

            if verif_created > 0:
                db.session.commit()
                logger.info(f"Created {verif_created} verification nodes for auto-verified edges")
        except Exception as e:
            logger.warning(f"Verification node creation skipped: {e}")

        return total_edges

    # ── Graph data ──────────────────────────────────────────────────
    def get_graph_data(self, days: int = 90, include_expired: bool = False, exclude_isolated: bool = True) -> dict:
        """Get full graph data for visualization.

        Args:
            days: Lookback window in days.
            include_expired: If True, include nodes past their expires_at.
            exclude_isolated: If True, exclude nodes with no causal edges (default True).
        """
        # Auto-build nodes if empty
        node_count = TimelineNode.query.count()
        if node_count == 0:
            logger.info("Timeline is empty, auto-building from events...")
            self.auto_build_from_events()
            # Re-fetch — auto_build_from_events() may have created nodes
            node_count = TimelineNode.query.count()

        # Auto-discover edges if nodes exist but no edges
        edge_count = CausalEdge.query.count()
        if node_count > 1 and edge_count == 0:
            logger.info("Timeline has nodes but no edges, auto-discovering links...")
            self._auto_discover_links()

        cutoff = datetime.now(timezone.utc)
        start = cutoff - timedelta(days=days)

        # Collect connected node IDs (nodes that have at least one edge)
        connected_ids: set = set()
        if exclude_isolated:
            all_edges = CausalEdge.query.all()
            for e in all_edges:
                connected_ids.add(e.source_node_id)
                connected_ids.add(e.target_node_id)

        query = TimelineNode.query.filter(TimelineNode.timestamp >= start)

        if not include_expired:
            query = query.filter(
                db.or_(
                    TimelineNode.expires_at.is_(None),      # backward compat: no expiry set
                    TimelineNode.expires_at >= cutoff,       # not yet expired
                )
            )

        if exclude_isolated and connected_ids:
            query = query.filter(TimelineNode.id.in_(connected_ids))

        nodes = (
            query
            .order_by(TimelineNode.timestamp.asc())
            .limit(200)
            .all()
        )
        node_ids = {n.id for n in nodes}

        edges = (
            CausalEdge.query
            .filter(
                CausalEdge.source_node_id.in_(node_ids),
                CausalEdge.target_node_id.in_(node_ids),
            )
            .all()
        )

        # Build vis.js format
        vis_nodes = []
        for n in nodes:
            color_map = {
                "root_event": "#3b82f6",
                "derived_event": "#8b5cf6",
                "prediction": "#f59e0b",
                "market_reaction": "#10b981",
                "verification": "#ef4444",
            }
            status_border = {
                "confirmed": "#22c55e",
                "predicted": "#eab308",
                "refuted": "#ef4444",
                "pending": "#9ca3af",
            }

            conf = n.confidence if n.confidence is not None else 0.5

            is_expired = False
            if n.expires_at is not None:
                now_utc = datetime.now(timezone.utc)
                if n.expires_at.tzinfo is None:
                    is_expired = n.expires_at.replace(tzinfo=timezone.utc) < now_utc
                else:
                    is_expired = n.expires_at < now_utc

            vis_nodes.append({
                "id": n.id,
                "label": (n.title or "")[:30],
                "description": (n.description or "")[:300],
                "title": f"<b>{n.title}</b><br>{n.description or ''}<br>状态: {n.status} | 置信度: {conf * 100:.0f}%",
                "color": {
                    "background": color_map.get(n.node_type, "#6b7280"),
                    "border": status_border.get(n.status, "#9ca3af"),
                },
                "node_type": n.node_type,
                "status": n.status,
                "confidence": conf,
                "timestamp": n.timestamp.isoformat() if n.timestamp else "",
                "tags": n.tags_json,
                "expires_at": n.expires_at.isoformat() if n.expires_at else None,
                "is_expired": is_expired,
            })

        vis_edges = []
        for e in edges:
            # Edge styling by verification status:
            #   verified=True  → solid green  (confirmed causal link)
            #   verified=False → red dashed   (confirmed refuted)
            #   verified=None  → solid gray   (inferred / not yet verified)
            if e.verified is True:
                edge_color = "#22c55e"
                dashes = False
            elif e.verified is False:
                edge_color = "#ef4444"
                dashes = True
            else:  # None — inferred, not yet verified
                edge_color = "#5c677d"
                dashes = False

            vis_edges.append({
                "id": e.id,
                "from": e.source_node_id,
                "to": e.target_node_id,
                "label": e.relation_type or "influences",
                "value": e.strength,
                "title": e.logic_chain or "",
                "dashes": dashes,
                "color": {"color": edge_color},
                "arrows": "to",
            })

        return {
            "nodes": vis_nodes,
            "edges": vis_edges,
            "stats": {
                "total_nodes": len(vis_nodes),
                "total_edges": len(vis_edges),
            },
        }

    # ── Cleanup ─────────────────────────────────────────────────────
    def cleanup_expired_nodes(self, older_than_days: int = 30) -> dict:
        """Physically delete expired timeline nodes past their expiration by at least `older_than_days`.

        CausalEdge rows are cascade-deleted via FK ON DELETE CASCADE.

        Args:
            older_than_days: Only delete nodes that have been expired for at least this many days.
                             Provides a safety buffer before permanent deletion.

        Returns:
            dict with counts: {"deleted_nodes": int, "remaining_expired": int}
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

        expired_query = TimelineNode.query.filter(
            TimelineNode.expires_at.isnot(None),
            TimelineNode.expires_at < cutoff,
        )

        count = expired_query.count()
        if count > 0:
            expired_query.delete(synchronize_session='fetch')
            db.session.commit()
            logger.info(f"Cleaned up {count} expired timeline nodes (expired before {cutoff.isoformat()})")

        remaining = TimelineNode.query.filter(
            TimelineNode.expires_at.isnot(None),
            TimelineNode.expires_at < datetime.now(timezone.utc),
        ).count()

        return {"deleted_nodes": count, "remaining_expired": remaining}

    # ── Snapshot ────────────────────────────────────────────────────
    def take_snapshot(self) -> Optional[TimelineSnapshot]:
        """Take a snapshot of the current graph state."""
        today = date.today()

        existing = TimelineSnapshot.query.filter_by(date=today).first()
        if existing:
            return existing

        graph = self.get_graph_data(days=365)
        snapshot = TimelineSnapshot(
            date=today,
            event_count=graph["stats"]["total_nodes"],
            edge_count=graph["stats"]["total_edges"],
            graph_json=graph,
        )
        db.session.add(snapshot)
        db.session.commit()
        return snapshot

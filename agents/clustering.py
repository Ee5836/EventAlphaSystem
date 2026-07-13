"""Event Clustering Agent — deduplicates and merges similar events."""
import json
import logging
from datetime import datetime, timezone

import numpy as np

from app.extensions import db
from agents.base import BaseAgent, AgentResult
from agents.stats_utils import (
    jaccard_similarity,
    temporal_decay,
    TagStatistics,
    encode_nodes_batch,
    semantic_similarity,
)

logger = logging.getLogger("agent.clustering")

CLUSTER_MERGE_PROMPT = """Merge the following events that report on the same topic into a single canonical event.
Events:
{events_json}

Generate a merged event JSON:
{{
  "canonical_title": "Merged title that captures the key information",
  "description": "A concise description combining all key details",
  "timeline": [
    {{"time": "ISO datetime", "description": "what happened"}}
  ],
  "confidence": 0.0-1.0
}}"""


class ClusteringAgent(BaseAgent):
    """Clusters and merges similar events using embedding similarity + concurrent LLM merge."""
    name = "clustering"

    def __init__(self, config=None):
        super().__init__(config)
        self._embedding_model = None
        self.similarity_threshold = 0.75

    def run(self, events: list = None, **kwargs) -> AgentResult:
        """Cluster raw events by multi-dimensional similarity and merge duplicates.

        Similarity now blends:
        - 50%: Semantic (title + description embedding cosine)
        - 25%: Tag overlap (Jaccard + NPMI boost)
        - 15%: Temporal proximity (exponential decay)
        - 10%: Entity overlap (Jaccard)

        Args:
            events: List of Event objects with status='raw'. If None, queries DB.

        Returns:
            AgentResult with output = {"clusters": [EventCluster], "merged_events": [Event]}
        """
        from models.event import Event, EventCluster, EventStatus
        from utils.concurrent import run_concurrently

        if events is None:
            events = Event.query.filter_by(status=EventStatus.RAW.value).all()

        if len(events) < 2:
            if events:
                events[0].status = EventStatus.CLUSTERED.value
                db.session.commit()
            return AgentResult(
                success=True,
                output={"clusters": [], "merged_events": events},
                metadata={"message": "Too few events to cluster"},
            )

        # ── Stage 1: Multi-dimensional similarity matrix ──
        # D1: Semantic similarity (title + description text2vec embeddings)
        texts = [
            (e.title or '').strip()
            for e in events
        ]
        embeddings = self._get_embeddings(texts)
        sem_sim = np.dot(embeddings, embeddings.T)  # cosine similarity matrix

        # D2: Tag Jaccard + NPMI matrix
        tag_matrix = np.zeros((len(events), len(events)))
        all_tags = [set(e.affected_industries_json or []) for e in events]
        # Build lightweight tag statistics for NPMI
        tag_stats = TagStatistics(events, tag_attr="affected_industries_json") if len(events) >= 3 else None
        for i in range(len(events)):
            for j in range(i + 1, len(events)):
                jac = jaccard_similarity(all_tags[i], all_tags[j])
                # NPMI boost when tag stats available
                if tag_stats is not None and all_tags[i] and all_tags[j]:
                    npmi_val = tag_stats.tag_set_npmi(all_tags[i], all_tags[j])
                    npmi_boost = (npmi_val + 1.0) / 2.0  # [-1,1] → [0,1]
                    tag_matrix[i][j] = tag_matrix[j][i] = 0.4 * jac + 0.6 * npmi_boost
                else:
                    tag_matrix[i][j] = tag_matrix[j][i] = jac

        # D3: Temporal proximity matrix
        time_matrix = np.zeros((len(events), len(events)))
        timestamps = [e.created_at for e in events]
        for i in range(len(events)):
            for j in range(i + 1, len(events)):
                if timestamps[i] and timestamps[j]:
                    t_i = timestamps[i]
                    t_j = timestamps[j]
                    if t_i.tzinfo is None:
                        t_i = t_i.replace(tzinfo=timezone.utc)
                    if t_j.tzinfo is None:
                        t_j = t_j.replace(tzinfo=timezone.utc)
                    delta_days = abs((t_i - t_j).total_seconds()) / 86400.0
                    # Events within 1 day → near 1.0; 7 days apart → ~0.5 (half-life=7d for clustering)
                    time_matrix[i][j] = time_matrix[j][i] = temporal_decay(delta_days, half_life_days=7.0)
                else:
                    time_matrix[i][j] = time_matrix[j][i] = 0.5  # neutral

        # D4: Entity overlap matrix (simple Jaccard)
        entity_matrix = np.zeros((len(events), len(events)))
        all_entities = [set(e.entities_json or []) for e in events]
        for i in range(len(events)):
            for j in range(i + 1, len(events)):
                entity_matrix[i][j] = entity_matrix[j][i] = jaccard_similarity(
                    all_entities[i], all_entities[j]
                )

        # ── Fused similarity matrix ──
        similarity_matrix = (
            0.50 * sem_sim +
            0.25 * tag_matrix +
            0.15 * time_matrix +
            0.10 * entity_matrix
        )
        # Ensure diagonal = 1 and clip to [0, 1]
        np.fill_diagonal(similarity_matrix, 1.0)
        similarity_matrix = np.clip(similarity_matrix, 0.0, 1.0)

        logger.info(
            f"Clustering similarity: semantic mean={sem_sim.mean():.3f}, "
            f"tag mean={tag_matrix.mean():.3f}, "
            f"time mean={time_matrix.mean():.3f}, "
            f"entity mean={entity_matrix.mean():.3f}, "
            f"fused mean={similarity_matrix.mean():.3f}"
        )

        clusters = self._agglomerative_cluster(events, similarity_matrix)

        # ── Stage 2: Build merge items for multi-event clusters ──
        # Single-event clusters skip LLM entirely
        merge_items = []
        cluster_map = {}  # merge index -> cluster_indices

        for ci, cluster_indices in enumerate(clusters):
            if len(cluster_indices) <= 1:
                continue
            cluster_events = [events[i] for i in cluster_indices]
            events_json = json.dumps([
                {"title": e.title, "type": e.event_type, "entities": e.entities_json}
                for e in cluster_events
            ], ensure_ascii=False, indent=2)
            merge_items.append({
                "cluster_index": ci,
                "events_json": events_json,
                "first_title": cluster_events[0].title,
            })
            cluster_map[ci] = cluster_indices

        # ── Stage 3: Concurrent LLM merge ──
        merged_results = {}
        if merge_items:
            llm = self._get_llm()
            max_workers = self.config.get("LLM_MAX_CONCURRENCY", 5)

            def _merge_cluster(item: dict):  # -> Optional[dict]
                """Worker: call LLM to merge a cluster."""
                try:
                    merged = llm.complete_json(
                        CLUSTER_MERGE_PROMPT.format(events_json=item["events_json"]),
                        "Merge these events.",
                        temperature=0.1,
                    )
                    return {
                        "cluster_index": item["cluster_index"],
                        "canonical_title": merged.get("canonical_title", item["first_title"]),
                        "description": merged.get("description", ""),
                    }
                except Exception as e:
                    logger.warning(f"LLM merge failed for cluster: {e}")
                    return {
                        "cluster_index": item["cluster_index"],
                        "canonical_title": item["first_title"],
                        "description": item["first_title"],
                    }

            successes, _ = run_concurrently(
                items=merge_items,
                worker_fn=_merge_cluster,
                max_workers=max_workers,
                description="cluster_merge",
            )
            merged_results = {s["cluster_index"]: s for s in successes}

        # ── Stage 4: Main-thread reconciliation ──
        merged_events = []
        for ci, cluster_indices in enumerate(clusters):
            if len(cluster_indices) == 1:
                idx = cluster_indices[0]
                events[idx].status = EventStatus.CLUSTERED.value
                merged_events.append(events[idx])
                continue

            cluster_events = [events[i] for i in cluster_indices]
            avg_sim = float(np.mean([
                similarity_matrix[i][j]
                for i in cluster_indices for j in cluster_indices if i < j
            ])) if len(cluster_indices) > 1 else 1.0

            merge = merged_results.get(ci, {})
            cluster = EventCluster(
                merged_event_ids=[e.id for e in cluster_events],
                similarity_score=avg_sim,
                canonical_title=merge.get("canonical_title", cluster_events[0].title),
                description=merge.get("description", ""),
            )
            db.session.add(cluster)
            db.session.flush()

            for event in cluster_events:
                event.cluster_id = cluster.id
                event.status = EventStatus.CLUSTERED.value
                tl = list(event.timeline_json or [])
                event.timeline_json = tl

            merged_events.extend(cluster_events)
            logger.info(
                f"Cluster: merged {len(cluster_events)} events → "
                f"'{cluster.canonical_title}'"
            )

        db.session.commit()

        return AgentResult(
            success=True,
            output={"clusters": clusters, "merged_events": merged_events},
            metadata={
                "total_events": len(events),
                "clusters_found": len(clusters),
                "events_merged": len(merged_events),
                "llm_merges": len(merge_items),
                "max_workers": self.config.get("LLM_MAX_CONCURRENCY", 5),
            },
        )

    def _get_embeddings(self, texts: list[str]) -> np.ndarray:
        """Get embeddings for texts using local model."""
        try:
            from sentence_transformers import SentenceTransformer
            if self._embedding_model is None:
                self._embedding_model = SentenceTransformer(
                    self.config.get("EMBEDDING_MODEL", "text2vec-base-chinese"),
                    local_files_only=True
                )
            return self._embedding_model.encode(texts, normalize_embeddings=True)
        except Exception:
            logger.error("Embedding model failed to load, using random embeddings as fallback — downstream similarity scores are UNRELIABLE")
            rng = np.random.RandomState(42)
            emb = rng.randn(len(texts), 768).astype(np.float32)
            emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
            return emb

    @staticmethod
    def _cosine_similarity(embeddings: np.ndarray) -> np.ndarray:
        """Compute cosine similarity matrix."""
        return np.dot(embeddings, embeddings.T)

    def _agglomerative_cluster(self, events, sim_matrix) -> list[list[int]]:
        """Simple threshold-based clustering."""
        n = len(events)
        visited = set()
        clusters = []

        for i in range(n):
            if i in visited:
                continue
            cluster = [i]
            visited.add(i)
            for j in range(i + 1, n):
                if j not in visited and sim_matrix[i][j] >= self.similarity_threshold:
                    cluster.append(j)
                    visited.add(j)
            clusters.append(cluster)

        return clusters

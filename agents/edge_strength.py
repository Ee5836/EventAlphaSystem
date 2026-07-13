"""Edge Strength Computation — multi-dimensional statistical correlation for Bubble.

Implements a 5-dimensional composite formula for quantifying event-to-event
correlation strength, replacing the original single-dimension tag-overlap heuristic.

Dimensions:
  D1 (35%): Semantic similarity — cosine similarity of title+description embeddings
  D2 (25%): Tag NPMI — normalized pointwise mutual information of shared tags
  D3 (15%): Jaccard — tag set overlap coefficient
  D4 (15%): Temporal decay — exponential decay by time distance
  D5 (10%): Event level — importance weighting by S/A/B/C grade

References:
  - Li (2025) "Causality mining for historical events based on KGs" — temporal decay β
  - Williams (2022) "On Suspicious Coincidences and PMI" — NPMI formulation
  - Wu & Xu (2025) "CKG-EIE" — confidence × node-importance edge weighting
  - Du Plessis (2024) "Text-Based Statistical Models" — text similarity + Granger causality
  - Naboka-Krell (2024) — SBERT embeddings for event correlation
  - Diks & Wolski (2024) — nonparametric tail-dependence tests
"""

import logging
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Lazy-loaded embedding model ──────────────────────────────────────
_embedding_model = None


def _get_embedding_model():
    """Lazy-load the local text2vec-base-chinese model (singleton)."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
        logger.info("Edge strength: loaded text2vec-base-chinese embedding model")
    return _embedding_model


# ── Event level weights — S-level events carry more causal weight ────
LEVEL_WEIGHT = {
    "S": 1.0,
    "A": 0.75,
    "B": 0.50,
    "C": 0.30,
    None: 0.40,  # unknown level
}

# ── Half-life constants (days) for temporal decay ────────────────────
# Financial events decay faster than historical events (Li 2025 uses 2.8yr half-life)
# We use shorter half-lives appropriate for investment-relevant event correlation
DEFAULT_HALF_LIFE_DAYS = 30.0  # default: weight halves after 30 days
HALF_LIFE_BY_LEVEL = {
    "S": 60.0,   # systemic events: slower decay
    "A": 30.0,   # significant events
    "B": 14.0,   # moderate events
    "C": 7.0,    # minor events: rapid decay
}
HALF_LIFE_BY_RELATION = {
    "causes": 45.0,       # causal: stronger temporal persistence
    "influences": 25.0,   # influence: moderate
    "correlates": 15.0,   # correlation: shorter
    "contradicts": 10.0,  # contradiction: very short relevance
}


# ══════════════════════════════════════════════════════════════════════
# Dimension 1: Semantic Similarity (cosine on text2vec embeddings)
# ══════════════════════════════════════════════════════════════════════

def encode_nodes_batch(nodes: list) -> dict:
    """Batch-encode node texts into embedding vectors.

    Args:
        nodes: list of objects with .title and .description attributes.

    Returns:
        dict mapping node id → numpy embedding vector (normalized).
    """
    model = _get_embedding_model()
    texts = []
    node_ids = []
    for n in nodes:
        title = (n.title or "").strip()
        desc = (getattr(n, "description", None) or "").strip()[:500]
        texts.append(f"{title} {desc}" if desc else title)
        node_ids.append(n.id)

    if not texts:
        return {}

    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return {nid: emb for nid, emb in zip(node_ids, embeddings)}


def semantic_similarity(emb_a: np.ndarray, emb_b: np.ndarray) -> float:
    """Cosine similarity between two normalized embedding vectors.

    Since vectors are already L2-normalized, dot product = cosine similarity.
    Maps from [-1, 1] to [0, 1] for use in the composite formula.
    """
    cos_sim = float(np.dot(emb_a, emb_b))
    # Map [-1, 1] → [0, 1]
    return max(0.0, min(1.0, (cos_sim + 1.0) / 2.0))


# ══════════════════════════════════════════════════════════════════════
# Dimension 2: NPMI (Normalized Pointwise Mutual Information)
# ══════════════════════════════════════════════════════════════════════

class TagStatistics:
    """Global tag co-occurrence statistics for NPMI computation.

    Computed once from all TimelineNode tags and cached for the lifetime
    of an edge-discovery run.
    """

    def __init__(self, all_nodes: list, tag_attr: str = "tags_json"):
        """Build tag frequency and co-occurrence tables from all nodes.

        Args:
            all_nodes: list of objects with a tag-list attribute.
            tag_attr: name of the attribute holding tags (default "tags_json").
                      Use "affected_industries_json" for Event objects.
        """
        self.total_docs = len(all_nodes)
        self.tag_doc_count: Counter = Counter()       # tag → doc frequency
        self.tag_pair_count: dict[tuple, int] = {}    # (tag_a, tag_b) → co-doc frequency

        for node in all_nodes:
            tags = list(set(getattr(node, tag_attr, None) or []))  # unique tags per node
            for tag in tags:
                self.tag_doc_count[tag] += 1
            # Count co-occurrences within the same document
            for i, ta in enumerate(tags):
                for j in range(i + 1, len(tags)):
                    tb = tags[j]
                    key = (ta, tb) if ta < tb else (tb, ta)
                    self.tag_pair_count[key] = self.tag_pair_count.get(key, 0) + 1

        self._npmi_cache: dict[tuple, float] = {}
        logger.debug(
            "TagStatistics: %d docs, %d unique tags, %d tag pairs",
            self.total_docs, len(self.tag_doc_count), len(self.tag_pair_count),
        )

    def npmi(self, tag_a: str, tag_b: str) -> float:
        """Compute Normalized PMI for a tag pair.

        NPMI(x; y) = PMI(x; y) / -log₂ P(x, y)  ∈  [-1, +1]
        """
        if tag_a == tag_b:
            return 1.0

        key = (tag_a, tag_b) if tag_a < tag_b else (tag_b, tag_a)
        if key in self._npmi_cache:
            return self._npmi_cache[key]

        if self.total_docs == 0:
            return 0.0

        p_a = self.tag_doc_count.get(tag_a, 0) / self.total_docs
        p_b = self.tag_doc_count.get(tag_b, 0) / self.total_docs
        p_ab = self.tag_pair_count.get(key, 0) / self.total_docs

        if p_ab == 0 or p_a == 0 or p_b == 0:
            result = 0.0
        else:
            pmi = math.log2(p_ab / (p_a * p_b))
            # Normalize: divide by -log₂(p_ab) = joint surprisal
            # Cap NPMI to [-1, 1]
            denominator = -math.log2(p_ab)
            if denominator == 0:
                result = 1.0 if pmi > 0 else -1.0
            else:
                result = max(-1.0, min(1.0, pmi / denominator))

        self._npmi_cache[key] = result
        return result

    def tag_set_npmi(self, tags_a: set, tags_b: set) -> float:
        """Compute the best NPMI score between any pair of tags from two sets.

        Strategy: take the maximum NPMI among cross-set tag pairs, which
        represents the strongest statistical association between the two events.

        Falls back to mean if no pair has a valid NPMI value.
        """
        values = []
        for ta in tags_a:
            for tb in tags_b:
                if ta != tb:
                    values.append(self.npmi(ta, tb))
        if not values:
            return 0.0
        # Use max: the strongest tag association dominates
        return max(values)


# ══════════════════════════════════════════════════════════════════════
# Dimension 3: Jaccard Similarity
# ══════════════════════════════════════════════════════════════════════

def jaccard_similarity(tags_a: set, tags_b: set) -> float:
    """Jaccard coefficient: |A ∩ B| / |A ∪ B|."""
    if not tags_a and not tags_b:
        return 0.0
    union = tags_a | tags_b
    if not union:
        return 0.0
    return len(tags_a & tags_b) / len(union)


# ══════════════════════════════════════════════════════════════════════
# Dimension 4: Temporal Decay
# ══════════════════════════════════════════════════════════════════════

def temporal_decay(
    delta_days: float,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Exponential temporal decay: w(Δt) = exp(-β × Δt).

    Uses half-life parametrization for interpretability:
        β = ln(2) / T₁/₂
        w(T₁/₂) = 0.5

    Args:
        delta_days: Absolute time difference in days.
        half_life_days: Days after which weight drops to 0.5.

    Returns:
        Decay weight ∈ (0, 1].
    """
    if delta_days < 0:
        delta_days = 0
    beta = math.log(2) / half_life_days
    return math.exp(-beta * delta_days)


def adaptive_half_life(
    level_a: Optional[str],
    level_b: Optional[str],
    relation_type: str = "correlates",
) -> float:
    """Compute an adaptive half-life based on both nodes' event levels.

    Higher-level events decay slower (their causal relevance persists longer).
    Also adjusts by relation type — causal links persist longer than correlations.

    Returns half-life in days.
    """
    # Base half-life from relation type
    base = HALF_LIFE_BY_RELATION.get(relation_type, DEFAULT_HALF_LIFE_DAYS)

    # Adjust by event levels — take the higher level
    level_half_lives = [
        HALF_LIFE_BY_LEVEL.get(lv, DEFAULT_HALF_LIFE_DAYS)
        for lv in (level_a, level_b)
    ]
    level_adjusted = max(level_half_lives)  # longer-lasting level dominates

    # Blend: 60% level-adjusted, 40% relation-type base
    return 0.6 * level_adjusted + 0.4 * base


# ══════════════════════════════════════════════════════════════════════
# Dimension 5: Event Level Bonus
# ══════════════════════════════════════════════════════════════════════

def level_bonus(level_a: Optional[str], level_b: Optional[str]) -> float:
    """Weighted average of both nodes' importance levels.

    S-level → 1.0, A → 0.75, B → 0.50, C → 0.30.
    """
    w_a = LEVEL_WEIGHT.get(level_a, LEVEL_WEIGHT[None])
    w_b = LEVEL_WEIGHT.get(level_b, LEVEL_WEIGHT[None])
    return (w_a + w_b) / 2.0


# ══════════════════════════════════════════════════════════════════════
# Wilson Score Interval (confidence lower bound)
# ══════════════════════════════════════════════════════════════════════

def wilson_lower_bound(successes: int, trials: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound (95% confidence by default).

    Used to assess the reliability of a proportion estimate when sample size
    is small. A small number of co-occurrence events yields a lower bound
    closer to 0, penalizing low-evidence correlations.

    Args:
        successes: Number of observed co-occurrences for a tag pair.
        trials: Total number of documents containing either tag.
        z: Z-score for desired confidence (1.96 = 95%).

    Returns:
        Lower bound ∈ [0, 1]. Conservative estimate of true proportion.
    """
    if trials == 0:
        return 0.0
    p = successes / trials
    z2 = z * z
    denominator = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denominator
    margin = z * math.sqrt(
        (p * (1 - p) + z2 / (4 * trials)) / trials
    ) / denominator
    return max(0.0, center - margin)


# ══════════════════════════════════════════════════════════════════════
# Composite Formula
# ══════════════════════════════════════════════════════════════════════

def compute_edge_strength(
    *,
    # Node data
    emb_a: Optional[np.ndarray] = None,
    emb_b: Optional[np.ndarray] = None,
    tags_a: Optional[set] = None,
    tags_b: Optional[set] = None,
    level_a: Optional[str] = None,
    level_b: Optional[str] = None,
    timestamp_a: Optional[datetime] = None,
    timestamp_b: Optional[datetime] = None,
    relation_type: str = "correlates",
    # Pre-computed statistics
    tag_stats: Optional[TagStatistics] = None,
    # Fallback values (when dimensions can't be computed)
    default_semantic: float = 0.40,
    default_tag_npmi: float = 0.0,
) -> float:
    """Compute multi-dimensional edge strength.

    Combines 5 dimensions into a single [0, 1] score:

    | Dimension | Weight | Method |
    |-----------|--------|--------|
    | D1 Semantic | 35% | cosine(text2vec(title+desc)) |
    | D2 Tag NPMI | 25% | max NPMI across cross-set tag pairs |
    | D3 Jaccard  | 15% | |A ∩ B| / |A ∪ B| |
    | D4 Temporal | 15% | exp(-β × Δt), adaptive half-life |
    | D5 Level    | 10% | average importance weight |

    Also applies a Wilson-based reliability adjustment when tag statistics
    are available: edges backed by few co-occurrence observations are
    slightly penalized.

    Returns:
        float ∈ [0.0, 1.0], rounded to 4 decimal places.
    """
    tags_a = tags_a or set()
    tags_b = tags_b or set()

    # ── D1: Semantic Similarity (35%) ──
    d1 = semantic_similarity(emb_a, emb_b) if emb_a is not None and emb_b is not None else default_semantic

    # ── D2: Tag NPMI (25%) ──
    if tag_stats is not None and tags_a and tags_b:
        d2_raw = tag_stats.tag_set_npmi(tags_a, tags_b)
        # Map NPMI [-1, 1] → [0, 1] for the composite formula
        d2 = (d2_raw + 1.0) / 2.0
    else:
        d2 = default_tag_npmi

    # ── D3: Jaccard (15%) ──
    d3 = jaccard_similarity(tags_a, tags_b)

    # ── D4: Temporal Decay (15%) ──
    if timestamp_a is not None and timestamp_b is not None:
        # Handle timezone-aware and naive datetimes
        t_a = timestamp_a
        t_b = timestamp_b
        if t_a.tzinfo is None:
            t_a = t_a.replace(tzinfo=timezone.utc)
        if t_b.tzinfo is None:
            t_b = t_b.replace(tzinfo=timezone.utc)
        delta_days = abs((t_a - t_b).total_seconds()) / 86400.0
        hl = adaptive_half_life(level_a, level_b, relation_type)
        d4 = temporal_decay(delta_days, half_life_days=hl)
    else:
        d4 = 0.5  # neutral when no timestamp

    # ── D5: Event Level Bonus (10%) ──
    d5 = level_bonus(level_a, level_b)

    # ── Weighted fusion ──
    strength = (
        0.35 * d1 +
        0.25 * d2 +
        0.15 * d3 +
        0.15 * d4 +
        0.10 * d5
    )

    # ── Wilson reliability adjustment (optional) ──
    # When tag stats are available, use Wilson lower bound of the
    # strongest tag pair's co-occurrence to scale down low-evidence edges.
    # This only applies when d2 is based on actual NPMI (not default).
    if tag_stats is not None and tags_a and tags_b:
        # Find the best tag pair for reliability assessment
        best_reliability = 0.5  # neutral default
        for ta in tags_a:
            if ta is None:
                continue
            for tb in tags_b:
                if tb is None or ta == tb:
                    continue
                key = (ta, tb) if ta < tb else (tb, ta)
                co_occur = tag_stats.tag_pair_count.get(key, 0)
                # trials ~ docs containing either tag
                trials = (
                    tag_stats.tag_doc_count.get(ta, 0) +
                    tag_stats.tag_doc_count.get(tb, 0)
                )
                if trials > 0:
                    rel = wilson_lower_bound(co_occur, max(trials, co_occur))
                    best_reliability = max(best_reliability, rel)

        # Blend: 90% raw strength + 10% reliability adjustment
        # If all tag pairs have low evidence, pull strength down slightly
        reliability_factor = 0.9 + 0.1 * best_reliability
        strength *= reliability_factor

    return round(max(0.0, min(1.0, strength)), 4)


# ══════════════════════════════════════════════════════════════════════
# Convenience: batch edge strength for rule-based discovery
# ══════════════════════════════════════════════════════════════════════

def build_edge_context(all_nodes: list) -> dict:
    """Pre-compute shared context for batch edge strength calculations.

    This avoids re-computing embeddings and tag statistics for every node pair.

    Args:
        all_nodes: list of TimelineNode objects.

    Returns:
        dict with keys: embeddings, tag_stats, ready for compute_edge_strength().
    """
    embeddings = encode_nodes_batch(all_nodes)
    tag_stats = TagStatistics(all_nodes)
    return {"embeddings": embeddings, "tag_stats": tag_stats}

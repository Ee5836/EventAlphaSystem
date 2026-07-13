"""Shared statistical utilities for Agent modules.

Re-exports core functions from edge_strength that are broadly applicable
across the system, plus domain-specific convenience helpers.
"""

from agents.edge_strength import (
    # Core math
    semantic_similarity,
    jaccard_similarity,
    temporal_decay,
    adaptive_half_life,
    level_bonus,
    wilson_lower_bound,
    # Tag statistics
    TagStatistics,
    # Embedding
    encode_nodes_batch,
    # Composite
    compute_edge_strength,
    # Constants
    LEVEL_WEIGHT,
    DEFAULT_HALF_LIFE_DAYS,
    HALF_LIFE_BY_LEVEL,
)

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Convenience: event timeliness (auto-decay based on age)
# ══════════════════════════════════════════════════════════════════════

def event_timeliness(
    event_timestamp: datetime,
    now: Optional[datetime] = None,
    half_life_days: float = 30.0,
) -> float:
    """Compute how timely/fresh an event is using exponential decay.

    Brand-new events → 1.0, month-old events → ~0.5, quarter-old → ~0.125.

    Useful as an automatic `timeliness_score` supplement/replacement
    in event scoring, search result ranking, etc.

    Args:
        event_timestamp: When the event occurred.
        now: Reference time (default: utcnow).
        half_life_days: Days after which timeliness drops to 0.5.

    Returns:
        float ∈ (0, 1].
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if event_timestamp.tzinfo is None:
        event_timestamp = event_timestamp.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta_days = (now - event_timestamp).total_seconds() / 86400.0
    if delta_days < 0:
        delta_days = 0
    return temporal_decay(delta_days, half_life_days=half_life_days)


# ══════════════════════════════════════════════════════════════════════
# Convenience: multi-dimensional event relevance score
# ══════════════════════════════════════════════════════════════════════

def event_relevance(
    *,
    event_title: str,
    event_description: str = "",
    event_tags: list = None,
    event_level: str = "B",
    event_timestamp: Optional[datetime] = None,
    target_text: str = "",
    target_tags: list = None,
    embedding_model=None,
    tag_stats: Optional[TagStatistics] = None,
) -> dict:
    """Compute event-to-target relevance across multiple dimensions.

    Suitable for: research assistant search ranking, event→stock matching,
    briefing event selection, etc.

    Returns:
        dict with keys: total, semantic, tag_overlap, timeliness, level,
        each float ∈ [0, 1].
    """
    event_tags = set(event_tags or [])
    target_tags = set(target_tags or [])

    # D1: Semantic similarity (50% weight for text matching)
    sem = 0.0
    if embedding_model is not None and event_title and target_text:
        try:
            texts = [f"{event_title} {event_description}"[:500], target_text[:500]]
            embs = embedding_model.encode(texts, normalize_embeddings=True)
            sem = semantic_similarity(embs[0], embs[1])
        except Exception:
            pass

    # D2: Tag overlap (25% weight)
    tag_overlap = jaccard_similarity(event_tags, target_tags) if event_tags and target_tags else 0.0

    # Boost tag overlap with NPMI when available
    npmi_boost = 0.0
    if tag_stats is not None and event_tags and target_tags:
        npmi_vals = []
        for et in event_tags:
            for tt in target_tags:
                if et != tt:
                    npmi_vals.append(tag_stats.npmi(et, tt))
        if npmi_vals:
            max_npmi = max(npmi_vals)
            npmi_boost = (max_npmi + 1.0) / 2.0  # map [-1,1] → [0,1]
            # Blend: tag_overlap gets NPMI boost
            tag_overlap = 0.5 * tag_overlap + 0.5 * npmi_boost

    # D3: Timeliness (15% weight)
    time_w = 0.5
    if event_timestamp is not None:
        hl = HALF_LIFE_BY_LEVEL.get(event_level, DEFAULT_HALF_LIFE_DAYS)
        time_w = event_timeliness(event_timestamp, half_life_days=hl)

    # D4: Level (10% weight)
    lv = LEVEL_WEIGHT.get(event_level, 0.4)

    # Weighted fusion
    total = 0.50 * sem + 0.25 * tag_overlap + 0.15 * time_w + 0.10 * lv

    return {
        "total": round(min(1.0, max(0.0, total)), 4),
        "semantic": round(sem, 4),
        "tag_overlap": round(tag_overlap, 4),
        "timeliness": round(time_w, 4),
        "level": round(lv, 4),
    }

"""Search result post-processors (pipeline pattern)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.services.rag.protocols import SearchResult

logger = logging.getLogger(__name__)


class RRFPostProcessor:
    """Reciprocal Rank Fusion for combining multiple ranked result lists.

    Generalizes the original 2-list RRF to N lists with per-backend weights.
    RRF score = weight / (k + rank), where k is a smoothing constant.
    """

    def __init__(self, k: int = 60, weights: Optional[Dict[str, float]] = None):
        self.k = k
        self.weights = weights or {}

    def fuse(
        self,
        ranked_lists: Dict[str, List[SearchResult]],
        *,
        top_k: int = 10,
    ) -> List[SearchResult]:
        """Fuse multiple ranked lists into one via RRF.

        IMPORTANT: For backward compatibility with the original 2-list
        implementation, the scoring formula must be exactly:
        score += weight / (k + rank + 1)
        where rank is 0-based.
        """
        scores: Dict[str, float] = {}
        result_map: Dict[str, SearchResult] = {}

        for backend_name, results in ranked_lists.items():
            weight = self.weights.get(backend_name, 1.0)
            for rank, result in enumerate(results):
                key = result.dedup_key
                rrf_score = weight / (self.k + rank + 1)
                scores[key] = scores.get(key, 0.0) + rrf_score
                if key not in result_map:
                    result_map[key] = result

        sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)

        results: List[SearchResult] = []
        for key in sorted_keys[:top_k]:
            result = result_map[key]
            # Copy to avoid mutating the original SearchResult objects
            from dataclasses import replace
            result = replace(result, score=scores[key])
            results.append(result)

        return results


class FreshnessDecayPostProcessor:
    """Apply freshness decay: boost recent results.

    Formula: score *= (relevance_weight + (1 - relevance_weight) * freshness)
    where freshness = 1.0 / (1.0 + age_days / half_life_days)

    Default: 80% relevance + 20% freshness, 60-day half-life.
    """

    def __init__(self, relevance_weight: float = 0.8, half_life_days: float = 60.0):
        if half_life_days <= 0:
            raise ValueError("half_life_days must be positive")
        self.relevance_weight = relevance_weight
        self.freshness_weight = 1.0 - relevance_weight
        self.half_life_days = half_life_days

    def process(
        self,
        results: List[SearchResult],
        *,
        top_k: int = 5,
    ) -> List[SearchResult]:
        now = datetime.now(timezone.utc)
        for result in results:
            if result.created_at:
                try:
                    age_secs = (now - result.created_at).total_seconds()
                except TypeError:
                    # Timezone-naive created_at -- skip freshness for this result
                    logger.debug("Skipping freshness decay: timezone-naive created_at for %s", result.dedup_key)
                    continue
                age_days = max(0.0, age_secs / 86400)
                freshness = 1.0 / (1.0 + age_days / self.half_life_days)
                result.score *= (self.relevance_weight + self.freshness_weight * freshness)

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]


class ModelMismatchWarner:
    """Log warnings when stored embeddings use a different model than the query.

    Does not modify results -- purely observational.
    """

    def __init__(self, query_model: Optional[str] = None):
        self.query_model = query_model

    def process(
        self,
        results: List[SearchResult],
        *,
        top_k: int = 5,
    ) -> List[SearchResult]:
        if not self.query_model:
            return results

        mismatched = [
            r for r in results
            if r.model and r.model != "unknown" and r.model != self.query_model
        ]
        if mismatched:
            logger.warning(
                "RAG: %d/%d results have mismatched embedding model "
                "(query=%s, stored=%s). Cosine similarity may be unreliable.",
                len(mismatched), len(results), self.query_model,
                {r.model for r in mismatched},
            )

        return results

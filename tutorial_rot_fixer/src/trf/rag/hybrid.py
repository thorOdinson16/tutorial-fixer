"""Reciprocal Rank Fusion: combine rankings by rank position, ignoring incomparable raw scores."""

from __future__ import annotations


def rrf(
    rankings: list[list[str]], *, k: int = 60, weights: list[float] | None = None
) -> list[tuple[str, float]]:
    """score(d) = sum_i w_i / (k + rank_i(d)), ranks 1-based. Ties break by id for determinism."""
    weights = weights or [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights must match rankings")
    scores: dict[str, float] = {}
    for ranking, w in zip(rankings, weights, strict=True):
        seen: set[str] = set()
        for rank, cid in enumerate(ranking, start=1):
            if cid in seen:
                continue
            seen.add(cid)
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))

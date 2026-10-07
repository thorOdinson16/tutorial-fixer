"""Cross-encoder reranking + version-proximity boost.

The boost encodes the task: the tutorial was written for `old`, we are migrating to `target`, so
chunks describing a change introduced in (old, target] matter most, changes at or before `old`
are already baked into the tutorial, and changes after `target` don't apply.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from packaging.version import InvalidVersion, Version

from trf.state import Chunk

DEFAULT_CE_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
BOOST_WEIGHT = 0.35
# Share of the upstream (BM25/dense/symbol-index fused) score kept alongside the cross-encoder.
# Pure cross-encoder (0.0) discarded exact-identifier evidence and hurt MRR on the retrieval
# sanity set; 0.3-0.7 all beat it, 0.5 is the middle of that plateau (tuned on 10 cases only).
UPSTREAM_BLEND = 0.5


class PairScorer(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


class CrossEncoderScorer:
    def __init__(self, model_name: str = DEFAULT_CE_MODEL):
        from sentence_transformers import CrossEncoder  # heavy import, kept lazy

        self.model = CrossEncoder(model_name)

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self.model.predict([(query, t) for t in texts])]


def parse_version(v: str | None) -> Version | None:
    if not v:
        return None
    try:
        return Version(v)
    except InvalidVersion:
        return None


def version_proximity(chunk: Chunk, old: str | None, target: str | None) -> float:
    """+1 in the old->target transition, +0.5 same-major-as-target, 0 neutral, -1 after target."""
    vi = parse_version(chunk.version_introduced)
    o, t = parse_version(old), parse_version(target)
    if vi is None:
        return 0.25 if chunk.doc_type == "migration" else 0.0
    if t and vi > t:
        return -1.0
    if o and t:
        if o < vi <= t:
            return 1.0
        return -0.25 if vi <= o else 0.0
    if t and vi.major == t.major:
        return 0.5
    return 0.0


def applies_to_target(chunk: Chunk, target: str | None) -> bool:
    """False for chunks that describe versions newer than the target (hard filter)."""
    vi, t = parse_version(chunk.version_introduced), parse_version(target)
    return not (vi and t and vi > t)


def _minmax(xs: list[float]) -> list[float]:
    """Scale to [0, 1] within the candidate pool. Cross-encoder outputs are raw logits whose
    absolute scale is arbitrary (a sigmoid saturates near 1.0 for everything), so only the
    relative order inside the pool is meaningful."""
    lo, hi = min(xs), max(xs)
    return [0.5] * len(xs) if hi == lo else [(x - lo) / (hi - lo) for x in xs]


def rerank(
    query: str,
    candidates: Sequence[tuple[Chunk, float]],
    *,
    scorer: PairScorer | None,
    use_version_boost: bool,
    old: str | None = None,
    target: str | None = None,
    weight: float = BOOST_WEIGHT,
    blend: float | None = None,
) -> list[tuple[Chunk, float]]:
    """Order (chunk, upstream_score) pairs; upstream scores are used only when `scorer` is None."""
    if not candidates:
        return []
    top = max(s for _, s in candidates) or 1.0
    upstream = [s / top for _, s in candidates]
    if scorer is not None:
        blend = UPSTREAM_BLEND if blend is None else blend
        ce = _minmax(scorer.score(query, [c.text for c, _ in candidates]))
        base = [(1 - blend) * c + blend * u for c, u in zip(ce, upstream, strict=True)]
    else:
        base = upstream
    scored = []
    for (chunk, _), b in zip(candidates, base, strict=True):
        boost = weight * version_proximity(chunk, old, target) if use_version_boost else 0.0
        scored.append((chunk, b + boost))
    return sorted(scored, key=lambda cs: (-cs[1], cs[0].id))

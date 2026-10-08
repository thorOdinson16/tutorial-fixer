"""Flag-driven retrieval pipeline so every stage can be ablated by the eval.

   symbol index ─┐
   BM25 ─────────┼─ RRF ─ (version filter) ─ cross-encoder ─ version boost ─ top-k
   dense ────────┘

Flags (PipelineFlags): use_symbol_index, use_bm25, use_dense, use_rerank, use_version_boost.
With every retrieval flag off the pipeline returns nothing ("LLM only" config).
Indexes and models are built lazily, so a disabled stage never loads its model.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from trf.config import PipelineFlags
from trf.rag.bm25 import BM25Index
from trf.rag.dense import DenseIndex, Encoder, SentenceTransformerEncoder
from trf.rag.hybrid import rrf
from trf.rag.rerank import CrossEncoderScorer, PairScorer, applies_to_target, rerank
from trf.rag.symbol_index import SymbolIndex, aliased_name
from trf.state import Chunk, Symbol

CANDIDATES = 50  # per retriever
RERANK_POOL = 30  # fused candidates sent to the cross-encoder
SYMBOL_WEIGHT = 1.5  # symbol-index hits are consulted first: they outweigh generic rankings


class Hit(BaseModel):
    chunk: Chunk
    score: float
    via: list[str]  # which stages surfaced it: symbol_index / bm25 / dense


def symbol_queries(symbol: Symbol) -> tuple[str, str]:
    """(keyword query for BM25, natural-language query for dense/rerank)."""
    m = re.match(r"^(?P<callee>.+)\((?P<arg>\w+)\)$", symbol.name)
    parts = [p for p in symbol.name.removeprefix(symbol.library + ".").split(".") if p != "*"]
    short = ".".join(parts)  # `pandas.*.append` -> `append`
    qualify = len(parts) == 1 and "*" not in symbol.name  # `pandas.datetime`, not `pandas.append`
    keywords = f"{symbol.library}.{short}" if qualify else short
    tail = ".".join(parts[-2:])  # `BaseModel.Config.orm_mode` -> `Config.orm_mode` for the reranker
    if m:
        callee = m["callee"].removeprefix(symbol.library + ".")
        return m["arg"] + " " + callee, f"What replaces the argument {m['arg']} of {callee}?"
    if symbol.kind == "cli":
        return symbol.name, f"What changed in the command {symbol.name}?"
    alias = aliased_name(f"{symbol.library}.{short}") if qualify else None
    if alias:  # release notes say `np.float`, not `numpy.float`
        keywords = f"{keywords} {alias}"
    return keywords, f"What replaces {tail} in {symbol.library}?"


class RetrievalPipeline:
    def __init__(
        self,
        chunks: list[Chunk],
        flags: PipelineFlags,
        *,
        encoder: Encoder | Callable[[], Encoder] | None = None,
        scorer: PairScorer | Callable[[], PairScorer] | None = None,
        cache_dir: Path | None = None,
        tag: str = "corpus",
    ):
        self.chunks = {c.id: c for c in chunks}
        self.flags = flags
        self._encoder, self._scorer = encoder, scorer
        self._cache_dir, self._tag = cache_dir, tag
        self._bm25: BM25Index | None = None
        self._dense: DenseIndex | None = None
        self._symbols: SymbolIndex | None = None
        self._ce: PairScorer | None = None

    # -- lazily built stages ----------------------------------------------------------
    def bm25(self) -> BM25Index:
        if self._bm25 is None:
            self._bm25 = BM25Index(list(self.chunks), [c.text for c in self.chunks.values()])
        return self._bm25

    def dense(self) -> DenseIndex:
        if self._dense is None:
            enc = self._encoder
            enc = enc() if callable(enc) and not hasattr(enc, "encode") else enc
            enc = enc or SentenceTransformerEncoder()
            self._dense = DenseIndex.build(
                list(self.chunks),
                [c.text for c in self.chunks.values()],
                enc,
                self._cache_dir,
                self._tag,
            )
        return self._dense

    def symbols(self) -> SymbolIndex:
        if self._symbols is None:
            self._symbols = SymbolIndex(list(self.chunks.values()))
        return self._symbols

    def scorer(self) -> PairScorer:
        if self._ce is None:
            s = self._scorer
            s = s() if callable(s) and not hasattr(s, "score") else s
            self._ce = s or CrossEncoderScorer()
        return self._ce

    # -- retrieval --------------------------------------------------------------------
    def retrieve(
        self,
        query: str,
        *,
        keywords: str | None = None,
        symbol: Symbol | None = None,
        old: str | None = None,
        target: str | None = None,
        k: int = 5,
    ) -> list[Hit]:
        f = self.flags
        rankings: list[list[str]] = []
        weights: list[float] = []
        via: dict[str, list[str]] = {}

        def add(stage: str, ranked: list[tuple[str, float]], weight: float = 1.0) -> None:
            if not ranked:
                return
            rankings.append([cid for cid, _ in ranked])
            weights.append(weight)
            for cid, _ in ranked:
                via.setdefault(cid, []).append(stage)

        if f.use_symbol_index and symbol is not None:
            add("symbol_index", self.symbols().lookup(symbol)[:CANDIDATES], SYMBOL_WEIGHT)
        if f.use_bm25:
            add("bm25", self.bm25().search(keywords or query, CANDIDATES))
        if f.use_dense:
            add("dense", self.dense().search(query, CANDIDATES))
        if not rankings:
            return []

        fused = rrf(rankings, weights=weights)
        cands = [(self.chunks[cid], s) for cid, s in fused]
        if f.use_version_boost:  # hard-drop chunks about versions newer than the target
            cands = [(c, s) for c, s in cands if applies_to_target(c, target)]
        pool = cands[:RERANK_POOL]
        ranked = rerank(
            query,
            pool,
            scorer=self.scorer() if f.use_rerank else None,
            use_version_boost=f.use_version_boost,
            old=old,
            target=target,
        )
        return [Hit(chunk=c, score=s, via=via[c.id]) for c, s in ranked[:k]]

    def retrieve_for_symbol(
        self, symbol: Symbol, *, old: str | None, target: str | None, k: int = 5
    ) -> list[Hit]:
        keywords, query = symbol_queries(symbol)
        return self.retrieve(query, keywords=keywords, symbol=symbol, old=old, target=target, k=k)

"""Per-symbol retrieval: fill `state.evidence[symbol.name]` with the top chunks.

In loop passes (loop_count > 0) only the symbols `query_rewrite` just derived from sandbox
errors are retrieved; earlier evidence is kept.
The pipeline factory is injectable so the node is testable without models or a corpus.
"""

from __future__ import annotations

from collections.abc import Callable

from trf.config import PipelineFlags, get_cache_dir
from trf.rag.corpus import corpus_path, load_corpus
from trf.rag.pipeline import RetrievalPipeline
from trf.state import GraphState

PipelineFactory = Callable[[str], RetrievalPipeline]


def default_factory(flags: PipelineFlags) -> PipelineFactory:
    cache: dict[str, RetrievalPipeline] = {}

    def factory(library: str) -> RetrievalPipeline:
        if library not in cache:
            cache_dir = get_cache_dir()
            chunks = load_corpus(corpus_path(library, cache_dir))
            cache[library] = RetrievalPipeline(chunks, flags, cache_dir=cache_dir, tag=library)
        return cache[library]

    return factory


def make_retrieve(
    factory: PipelineFactory | None = None, flags: PipelineFlags | None = None, k: int = 5
) -> Callable[[GraphState], dict]:
    fac = factory or default_factory(flags or PipelineFlags.from_env())

    def retrieve(state: GraphState) -> dict:
        evidence = dict(state.evidence)
        fresh = set(state.fresh_symbols)
        for s in state.symbols:
            if state.loop_count > 0 and s.name not in fresh:
                continue
            target = state.target_versions.get(s.library)
            if target is None:  # not a library we migrate
                continue
            hits = fac(s.library).retrieve_for_symbol(
                s, old=state.old_versions.get(s.library), target=target, k=k
            )
            evidence[s.name] = [h.chunk for h in hits]
        return {"evidence": evidence}

    return retrieve


_default: Callable[[GraphState], dict] | None = None


def retrieve(state: GraphState) -> dict:
    """Default graph node: env-configured flags, corpus from the cache dir (built once)."""
    global _default
    if _default is None:
        _default = make_retrieve()
    return _default(state)

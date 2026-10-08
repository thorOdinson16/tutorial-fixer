"""LangGraph wiring.

ingest -> extract -> resolve_versions -> retrieve -> diff -> rewrite -> sandbox, then
sandbox -> assemble (all pass / cap hit) or sandbox -> query_rewrite -> retrieve (failures,
under cap); query_rewrite -> assemble when no failing block names a library symbol.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from langgraph.graph import END, START, StateGraph

from trf.config import PipelineFlags
from trf.llm import LLM
from trf.nodes import (
    assemble,
    diff,
    extract,
    ingest,
    query_rewrite,
    resolve_versions,
    retrieve,
    rewrite,
    sandbox,
)
from trf.state import GraphState

Node = Callable[[GraphState], dict]


def default_nodes(flags: PipelineFlags | None = None, llm: LLM | None = None) -> dict[str, Node]:
    """The real nodes, configured by `flags`. LLM/corpus are only loaded when a node needs them."""
    flags = flags or PipelineFlags()
    return {
        "ingest": ingest.ingest,
        "extract": extract.extract,
        "resolve_versions": resolve_versions.resolve_versions,
        "retrieve": retrieve.make_retrieve(flags=flags),
        "diff": diff.make_diff(llm, flags),
        "rewrite": rewrite.make_rewrite(llm, flags),
        "sandbox": sandbox.sandbox,
        "query_rewrite": query_rewrite.query_rewrite,
        "assemble": assemble.assemble,
    }


RECURSION_LIMIT = 200


def route_after_sandbox(
    state: GraphState, flags: PipelineFlags | None = None
) -> Literal["query_rewrite", "assemble"]:
    flags = flags or PipelineFlags()
    failing = [b for b in state.blocks if b.status == "failing"]
    if not failing or not flags.use_sandbox_loop or state.loop_count >= state.max_loops:
        return "assemble"
    return "query_rewrite"


def route_after_query_rewrite(
    state: GraphState, flags: PipelineFlags | None = None
) -> Literal["retrieve", "assemble"]:
    """Loop again only if a failing block has something to act on (nothing to retry otherwise)."""
    flags = flags or PipelineFlags()
    failing = [b for b in state.blocks if b.status == "failing"]
    if not flags.retrieval_enabled:  # no-retrieval config: rewrite retries from the error alone
        return "retrieve" if failing else "assemble"
    return "retrieve" if any(state.error_symbols.get(b.id) for b in failing) else "assemble"


def build_graph(
    nodes: dict[str, Node] | None = None,
    flags: PipelineFlags | None = None,
    llm: LLM | None = None,
):
    """Compile the graph. `nodes` overrides individual nodes (used by tests)."""
    impl = {**default_nodes(flags, llm), **(nodes or {})}
    g = StateGraph(GraphState)
    for name, fn in impl.items():
        g.add_node(name, fn)

    g.add_edge(START, "ingest")
    for a, b in [
        ("ingest", "extract"),
        ("extract", "resolve_versions"),
        ("resolve_versions", "retrieve"),
        ("retrieve", "diff"),
        ("diff", "rewrite"),
        ("rewrite", "sandbox"),
    ]:
        g.add_edge(a, b)
    g.add_conditional_edges(
        "sandbox",
        lambda s: route_after_sandbox(s, flags),
        {"query_rewrite": "query_rewrite", "assemble": "assemble"},
    )
    g.add_conditional_edges(
        "query_rewrite",
        lambda s: route_after_query_rewrite(s, flags),
        {"retrieve": "retrieve", "assemble": "assemble"},
    )
    g.add_edge("assemble", END)
    return g.compile()


def run_graph(state: GraphState, **kwargs) -> GraphState:
    result = build_graph(**kwargs).invoke(state, {"recursion_limit": RECURSION_LIMIT})
    return GraphState.model_validate(result)

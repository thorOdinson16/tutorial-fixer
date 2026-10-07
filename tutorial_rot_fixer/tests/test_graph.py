from trf.config import PipelineFlags
from trf.graph import build_graph, route_after_sandbox, run_graph
from trf.state import CodeBlock, GraphState


def _passing_sandbox(state):
    blocks = [b.model_copy(update={"status": "passing"}) for b in state.blocks]
    return {"blocks": blocks, "loop_count": state.loop_count + 1}


def test_hello_world_graph():
    """Real ingest/extract/resolve (no libs, so no network); sandbox stubbed (no Docker)."""
    md = "# hi\n\n```python\nprint(1)\n```\n"
    out = run_graph(GraphState(tutorial_md=md), nodes={"sandbox": _passing_sandbox})
    assert out.output_md == md
    assert [b.status for b in out.blocks] == ["passing"]
    assert out.loop_count == 1


def _calls_recorder(order):
    def mk(name):
        def node(state):
            order.append(name)
            return {}

        return node

    return {
        n: mk(n) for n in ["ingest", "extract", "resolve_versions", "retrieve", "diff", "rewrite"]
    }


def _sandbox_failing_until(n_passes_at):
    def sandbox(state):
        count = state.loop_count + 1
        status = "passing" if count >= n_passes_at else "failing"
        blocks = [b.model_copy(update={"status": status}) for b in state.blocks]
        return {"blocks": blocks, "loop_count": count}

    return sandbox


def test_cycle_runs_until_pass():
    order = []
    nodes = {**_calls_recorder(order), "sandbox": _sandbox_failing_until(3)}
    out = build_graph(nodes).invoke(
        GraphState(blocks=[CodeBlock.new("b1", "x")]), {"recursion_limit": 100}
    )
    assert out["loop_count"] == 3
    assert order.count("retrieve") == 3  # initial pass + 2 loops
    assert out["blocks"][0].status == "passing"


def test_cycle_stops_at_cap():
    nodes = {**_calls_recorder([]), "sandbox": _sandbox_failing_until(999)}
    out = build_graph(nodes).invoke(
        GraphState(blocks=[CodeBlock.new("b1", "x")], max_loops=4), {"recursion_limit": 100}
    )
    assert out["loop_count"] == 4
    assert out["blocks"][0].status == "unresolved"  # assemble never reports it as passing
    assert out["unresolved_block_ids"] == ["b1"]


def test_router_respects_flag_and_state():
    failing = GraphState(blocks=[CodeBlock.new("b", "x").model_copy(update={"status": "failing"})])
    assert route_after_sandbox(failing) == "retrieve"
    assert route_after_sandbox(failing, PipelineFlags(use_sandbox_loop=False)) == "assemble"
    assert route_after_sandbox(failing.model_copy(update={"loop_count": 4})) == "assemble"
    assert route_after_sandbox(GraphState()) == "assemble"

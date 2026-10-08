"""The sandbox feedback loop end to end: real nodes, scripted LLM, scripted sandbox (no Docker)."""

from types import SimpleNamespace

from rag_fixtures import chunk
from test_diff_rewrite_assemble import MD, FakeLLM
from trf.config import PipelineFlags
from trf.graph import build_graph
from trf.nodes.diff import DiffBatch, VerdictOut, make_diff
from trf.nodes.retrieve import make_retrieve
from trf.nodes.rewrite import ChangeOut, RewriteOut, make_rewrite
from trf.state import GraphState

FLAGS = PipelineFlags(use_bm25=True, use_dense=False, use_symbol_index=False)
EV = chunk("pandas:wn:1", "Removed `DataFrame.append`, use `concat`", "2.0.0", lib="pandas")
FIXED = 'import pandas as pd\ndf = pd.DataFrame({"a": [1]})\ndf = pd.concat([df, pd.DataFrame([{"a": 2}])])\nprint(len(df))\n'


class FakePipeline:
    def __init__(self):
        self.asked: list[str] = []

    def retrieve_for_symbol(self, symbol, *, old, target, k):
        self.asked.append(symbol.name)
        return [SimpleNamespace(chunk=EV)]

    def retrieve(self, query, *, keywords, symbol, old, target, k):
        self.asked.append(f"error-query:{symbol.name}")
        return [SimpleNamespace(chunk=EV)]


def resolve(state):
    return {"old_versions": {"pandas": "1.3.5"}, "target_versions": {"pandas": "2.3.3"}}


def scripted_sandbox(state):
    """Fails with the real pandas error until the append is gone."""
    blocks = []
    for b in state.blocks:
        if b.lang != "python":
            blocks.append(b)
        elif ".append(" in b.source:
            err = "AttributeError: 'DataFrame' object has no attribute 'append'"
            blocks.append(b.model_copy(update={"status": "failing", "last_error": err}))
        else:
            blocks.append(b.model_copy(update={"status": "passing", "last_error": None}))
    return {"blocks": blocks, "loop_count": state.loop_count + 1}


def run(llm, pipeline, max_loops=4):
    nodes = {
        "resolve_versions": resolve,
        "retrieve": make_retrieve(lambda lib: pipeline, FLAGS),
        "diff": make_diff(llm, FLAGS),
        "rewrite": make_rewrite(llm, FLAGS),
        "sandbox": scripted_sandbox,
    }
    out = build_graph(nodes, FLAGS).invoke(
        GraphState(tutorial_md=MD, max_loops=max_loops), {"recursion_limit": 100}
    )
    return GraphState.model_validate(out)


def test_loop_recovers_a_symbol_the_first_diff_pass_cleared():
    verdict = VerdictOut(
        symbol="pandas.DataFrame.append",
        verdict="removed",
        replacement="pandas.concat",
        evidence_chunk_ids=["pandas:wn:1"],
        confidence=0.9,
    )
    llm = FakeLLM(
        DiffBatch(verdicts=[]),  # pass 0: model finds nothing -> block is not rewritten
        DiffBatch(verdicts=[verdict]),  # loop: told the sandbox error, now classifies it
        RewriteOut(
            code=FIXED, changes=[ChangeOut(symbol="pandas.DataFrame.append", description="concat")]
        ),
    )
    pipe = FakePipeline()
    out = run(llm, pipe)

    assert [b.status for b in out.blocks if b.lang == "python"] == ["passing"]
    assert out.unresolved_block_ids == []
    assert out.loop_count == 2
    assert "pd.concat" in out.output_md
    assert out.change_log and all(
        e.evidence_chunk_ids for e in out.change_log if e.kind == "api_change"
    )
    # the loop retrieved only the error's symbol again, and the diff prompt carried the failure
    assert pipe.asked.count("pandas.DataFrame.append") == 2
    assert "OBSERVED FAILURES" in llm.prompts[1]
    assert "no attribute 'append'" in llm.prompts[1]


def test_loop_gives_up_cleanly_when_the_model_never_fixes_it():
    nothing = DiffBatch(verdicts=[])
    out = run(FakeLLM(nothing, nothing, nothing, nothing, nothing), FakePipeline(), max_loops=3)
    # no verdict ever exists, so no cited rewrite is allowed: the block must end unresolved
    assert len(out.unresolved_block_ids) == 1
    assert all(b.status != "passing" for b in out.blocks if b.lang == "python")
    assert out.loop_count <= 3

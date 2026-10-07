import pytest
from pydantic import ValidationError

from trf.state import ChangeLogEntry, CodeBlock, DiffVerdict, GraphState, RunResult


def test_verdict_requires_evidence():
    with pytest.raises(ValidationError):
        DiffVerdict(symbol="parse_obj", verdict="renamed", evidence_chunk_ids=[], confidence=0.9)
    v = DiffVerdict(
        symbol="parse_obj", verdict="renamed", replacement="model_validate",
        evidence_chunk_ids=["c1"], confidence=0.9,
    )  # fmt: skip
    assert v.evidence_chunk_ids == ["c1"]


def test_verdict_confidence_bounds():
    with pytest.raises(ValidationError):
        DiffVerdict(symbol="x", verdict="removed", evidence_chunk_ids=["c"], confidence=1.5)


def test_changelog_requires_evidence():
    with pytest.raises(ValidationError):
        ChangeLogEntry(block_id="b1", description="x", evidence_chunk_ids=[])


def test_block_keeps_original():
    b = CodeBlock.new("b1", "print(1)")
    assert b.original_source == b.source and b.status == "pending"


def test_run_result_ok():
    assert RunResult(block_id="b", exit_code=0).ok
    assert not RunResult(block_id="b", exit_code=1).ok
    assert not RunResult(block_id="b", exit_code=0, passed_assertions=False).ok
    assert not RunResult(block_id="b", exit_code=0, timed_out=True).ok


def test_graph_state_defaults():
    s = GraphState()
    assert s.max_loops == 4 and s.loop_count == 0 and s.blocks == []


def test_changelog_kinds():
    ChangeLogEntry(
        block_id="b", kind="version_pin", description="x", source_urls=["https://pypi.org"]
    )
    ChangeLogEntry(block_id="b", kind="llm_prior", description="uncited baseline edit")
    with pytest.raises(ValidationError):
        ChangeLogEntry(block_id="b", kind="version_pin", description="x")
    with pytest.raises(ValidationError):
        ChangeLogEntry(block_id="b", kind="dependency", description="x")

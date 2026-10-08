"""precheck: find blocks that already work, and keep rewrite away from them (no Docker needed)."""

from test_diff_rewrite_assemble import RETRIEVAL, FakeLLM, prepared, with_evidence
from trf.config import PipelineFlags
from trf.eval.run_eval import CONFIGS
from trf.nodes.extract import packages_for_imports
from trf.nodes.precheck import compare, make_precheck, old_python
from trf.nodes.rewrite import make_rewrite
from trf.state import CodeBlock, DiffVerdict, GraphState, RunResult

ON = PipelineFlags(protect_working_blocks=True)


def ran(block_id, stdout="", ok=True):
    return RunResult(block_id=block_id, exit_code=0 if ok else 1, stdout=stdout)


def state_with(results, ids=("b1", "b2", "b3")):
    return GraphState(
        blocks=[CodeBlock.new(i, "x") for i in ids],
        run_results=results,
        libraries=["pandas"],
        old_versions={"pandas": "1.2.4"},
    )


def test_old_python_matches_what_the_old_wheels_support():
    assert old_python({"pandas": "1.0.5"}) == "3.8"
    assert old_python({"pandas": "1.2.4"}) == "3.9"
    assert old_python({"pandas": "1.3.3"}) == "3.9"
    assert old_python({"pandas": "1.3.4"}) == "3.10"
    assert old_python({"pandas": "1.5.3"}) == "3.10"
    assert old_python({"pydantic": "1.8.2"}) == "3.10"  # unchanged for pydantic
    assert old_python({}) == "3.10"
    assert old_python({"pandas": "not-a-version"}) == "3.10"


def test_compare_protects_working_blocks_and_flags_silent_behaviour_changes():
    target = state_with([ran("b1", "a"), ran("b2", "same"), ran("b3", ok=False)])
    old = state_with([ran("b1", "a"), ran("b2", "before"), ran("b3", "ok")])
    protected, drift = compare(target, old)
    assert protected == ["b1"]  # b1 same output
    assert drift == ["b2"]  # runs on both, prints different things: NOT protected
    # b3 fails in the target env, so it is neither


def test_compare_without_an_old_run_protects_on_target_success_alone():
    target = state_with([ran("b1"), ran("b2", ok=False)], ids=("b1", "b2"))
    assert compare(target, None) == (["b1"], [])


def test_compare_block_that_never_ran_in_the_old_env_is_protected_if_it_runs_now():
    target = state_with([ran("b1", "x")], ids=("b1",))
    old = state_with([ran("b1", "", ok=False)], ids=("b1",))
    assert compare(target, old) == (["b1"], [])


def test_node_is_a_no_op_unless_the_flag_is_on():
    def boom(*a):
        raise AssertionError("must not run the sandbox")

    assert make_precheck(PipelineFlags(), runner=boom)(prepared()) == {}


def test_node_returns_protected_blocks_and_warnings():
    s = prepared()
    py = [b.id for b in s.blocks if b.lang == "python"]
    calls = []

    def runner(state, mode, settings):
        calls.append(mode)
        out = "1" if mode == "target" else "2"
        return state.model_copy(update={"run_results": [ran(i, out) for i in py]})

    out = make_precheck(ON, runner=runner, settings=object())(s)
    assert calls == ["target", "old"]
    assert out["protected_blocks"] == []
    assert any("print different output" in w for w in out["warnings"])


def test_node_survives_an_unknown_old_version():
    s = prepared()
    py = [b.id for b in s.blocks if b.lang == "python"]

    def runner(state, mode, settings):
        if mode == "old":
            raise SystemExit("no old version known for ['pandas']; cannot run control")
        return state.model_copy(update={"run_results": [ran(i) for i in py]})

    out = make_precheck(ON, runner=runner, settings=object())(s)
    assert out["protected_blocks"] == py
    assert any("old environment not run" in w for w in out["warnings"])


def test_rewrite_never_touches_a_protected_block():
    s = with_evidence(prepared())
    s = s.model_copy(
        update={
            "verdicts": [
                DiffVerdict(
                    symbol="pandas.DataFrame.append",
                    verdict="removed",
                    evidence_chunk_ids=["pandas:wn:1"],
                    confidence=0.9,
                )
            ],
            "protected_blocks": ["b2"],
        }
    )
    llm = FakeLLM()  # no scripted replies: any LLM call would raise
    out = make_rewrite(llm, RETRIEVAL)(s)
    assert llm.prompts == []
    assert [b.source for b in out["blocks"] if b.id == "b2"] == [
        b.source for b in s.blocks if b.id == "b2"
    ]


def test_project_local_imports_are_not_pip_packages():
    assert packages_for_imports({"app", "utils", "fastapi", "schemas"}) == ["fastapi"]
    assert "requests" in packages_for_imports({"requests", "app"})


def test_protect_config_differs_from_5_loop_only_by_the_flag():
    a, b = CONFIGS["5_loop"].model_dump(), CONFIGS["6_protect"].model_dump()
    assert a["protect_working_blocks"] is False and b["protect_working_blocks"] is True
    assert {k: v for k, v in a.items() if k != "protect_working_blocks"} == {
        k: v for k, v in b.items() if k != "protect_working_blocks"
    }
    assert PipelineFlags().protect_working_blocks is False  # existing behaviour is the default


def test_change_log_lists_blocks_whose_output_changed_vs_the_original():
    from trf.nodes.assemble import build_change_log

    blocks = [
        CodeBlock.new("b1", "x").model_copy(update={"status": "passing"}),
        CodeBlock.new("b2", "y").model_copy(update={"status": "passing"}),
        CodeBlock.new("b3", "z").model_copy(update={"status": "failing"}),
    ]
    s = GraphState(
        blocks=blocks,
        old_outputs={"b1": "same\n", "b2": "before", "b3": "x"},
        run_results=[ran("b1", "same"), ran("b2", "after"), ran("b3", "", ok=False)],
    )
    md = build_change_log(s, blocks)
    assert "## Output compared with the original" in md
    assert "1 of 2 comparable blocks print the same output" in md
    assert "Different output: b2" in md
    assert "1 block(s) could not be compared" in md
    # not shown at all when the precheck did not run
    assert "Output compared" not in build_change_log(
        s.model_copy(update={"old_outputs": {}}), blocks
    )

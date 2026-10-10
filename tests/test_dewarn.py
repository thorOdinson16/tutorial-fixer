"""Warning capture and the fix_warnings stage (fake stages and sandbox; one real Docker check)."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from trf.config import PipelineFlags, SandboxSettings
from trf.nodes.assemble import build_change_log
from trf.nodes.dewarn import classify, make_dewarn, symbols_for_warning, warned_blocks
from trf.nodes.precheck import make_precheck
from trf.nodes.sandbox import (
    build_driver,
    docker_available,
    interpret,
    run_offline,
    split_warnings,
)
from trf.state import ChangeLogEntry, CodeBlock, GraphState, RunResult

ON = PipelineFlags(fix_warnings=True)
CODE = (
    "import pandas as pd\n"
    "df = pd.DataFrame({'a': ['x', 'yy']})\n"
    "print(df.applymap(len).to_dict())\n"
)
FIXED = CODE.replace("applymap", "map")
MSG = "DataFrame.applymap has been deprecated. Use DataFrame.map instead."
WARN = {"category": "FutureWarning", "message": MSG, "line": "3"}


def test_classify_deprecation_security_and_other():
    assert classify({"category": "FutureWarning", "message": "x changes"}) == "deprecation"
    assert classify({"category": "DeprecationWarning", "message": "x"}) == "deprecation"
    assert classify({"category": "UserWarning", "message": "y is deprecated"}) == "deprecation"
    assert classify({"category": "PydanticDeprecatedSince20", "message": "m"}) == "deprecation"
    assert classify({"category": "InsecureRequestWarning", "message": "Unverified HTTPS"}) == (
        "security"
    )
    assert classify({"category": "UserWarning", "message": "disabling certificate checks"}) == (
        "security"
    )
    assert classify({"category": "UserWarning", "message": "just a note"}) is None
    assert classify({"category": "RuntimeWarning", "message": "divide by zero"}) is None


def test_symbols_for_warning_ties_a_message_to_the_blocks_own_symbols():
    used = {"pandas.DataFrame.applymap", "pandas.DataFrame", "pandas.Series.sum(level)"}
    assert symbols_for_warning(MSG, used) == ["pandas.DataFrame.applymap"]
    assert symbols_for_warning("The 'level' keyword is deprecated", used) == [
        "pandas.Series.sum(level)"
    ]
    assert symbols_for_warning("something unrelated is deprecated", used) == []


def test_split_warnings_extracts_markers_and_cleans_stderr():
    w = json.dumps({"category": "FutureWarning", "message": "m", "line": "3"})
    text = f"Traceback...\n<<<TRF:WARN>>>{w}\nValueError: x\n"
    clean, found = split_warnings(text)
    assert found == [{"category": "FutureWarning", "message": "m", "line": "3"}]
    assert "TRF:WARN" not in clean and "ValueError: x" in clean
    assert split_warnings("<<<TRF:WARN>>>{not json")[1] == []


def test_interpret_attaches_the_recorded_warnings_to_each_block():
    w = json.dumps(WARN)
    raw = {
        "stdout": "<<<TRF:BEGIN:b2>>>\nhi\n",
        "stderr": f"<<<TRF:BEGIN:b2>>>\n<<<TRF:WARN>>>{w}\n",
    }
    blocks = [CodeBlock.new("b2", "print('hi')")]
    results, updates = interpret(blocks, {}, raw)
    assert results[0].warnings == [WARN] and results[0].ok
    assert updates["b2"][0] == "passing"
    assert results[0].stderr == ""  # the marker is not part of the block's stderr


needs_docker = pytest.mark.skipif(
    bool(os.environ.get("TRF_SKIP_DOCKER_TESTS")) or not docker_available(),
    reason="Docker daemon not available (or TRF_SKIP_DOCKER_TESTS is set)",
)


def ensure_test_image() -> None:
    """Build the sandbox test image only if it is missing; skip (not fail) if it cannot be built."""
    runner_dir = Path(__file__).parents[1] / "src" / "trf" / "sandbox_runner"
    have = (
        subprocess.run(
            ["docker", "image", "inspect", "trf-sandbox:test"], capture_output=True
        ).returncode
        == 0
    )
    if have:
        return
    built = subprocess.run(
        ["docker", "build", "-t", "trf-sandbox:test", str(runner_dir)], capture_output=True
    )
    if built.returncode != 0:
        pytest.skip("could not build the sandbox test image")


@pytest.mark.docker
@needs_docker
def test_the_real_driver_records_warnings_per_block():
    ensure_test_image()
    blocks = [
        CodeBlock.new("b1", "import warnings\nwarnings.warn('old() is deprecated', DeprecationWarning)\nprint(1)\n"),
        CodeBlock.new("b2", "print(2)\n"),
    ]  # fmt: skip
    raw = run_offline(
        build_driver(blocks, {}), SandboxSettings(image="trf-sandbox:test", timeout_s=20)
    )
    results, _ = interpret(blocks, {}, raw)
    by = {r.block_id: r for r in results}
    assert by["b1"].ok and by["b1"].stdout.strip() == "1"
    assert [(w["category"], w["message"]) for w in by["b1"].warnings] == [
        ("DeprecationWarning", "old() is deprecated")
    ]
    assert by["b2"].warnings == []


# --- the stage, with fake retrieve/diff/rewrite/sandbox --------------------------------------


def run_result(bid, stdout="{'a': 1}", warnings=()):
    return RunResult(block_id=bid, exit_code=0, stdout=stdout, warnings=list(warnings))


def base_state():
    blocks = [
        CodeBlock.new("b2", CODE).model_copy(update={"status": "passing"}),
        CodeBlock.new("b3", "print('ok')\n").model_copy(update={"status": "passing"}),
    ]
    return GraphState(
        blocks=blocks,
        libraries=["pandas"],
        run_results=[run_result("b2", warnings=[WARN]), run_result("b3", "ok")],
    )


def parts(new_source, after_results, entries=()):
    def rewrite(state):
        blocks = [
            b.model_copy(update={"source": new_source, "status": "pending", "last_error": None})
            if b.id == "b2"
            else b
            for b in state.blocks
        ]
        return {"blocks": blocks, "change_log": [*state.change_log, *entries]}

    def sandbox(state):
        blocks = [
            b.model_copy(update={"status": "passing", "last_error": None})
            if b.lang == "python"
            else b
            for b in state.blocks
        ]
        return {"blocks": blocks, "run_results": list(after_results)}

    return {"retrieve": lambda s: {}, "diff": lambda s: {}, "rewrite": rewrite, "sandbox": sandbox}


ENTRY = ChangeLogEntry(
    block_id="b2", symbol="pandas.DataFrame.applymap", description="applymap -> map",
    evidence_chunk_ids=["c1"],
)  # fmt: skip


def test_a_verified_fix_is_kept_and_logged_as_a_warning_fix():
    ok = parts(FIXED, [run_result("b2"), run_result("b3", "ok")], [ENTRY])
    out = make_dewarn(ON, ok)(base_state())
    b2 = next(b for b in out["blocks"] if b.id == "b2")
    assert b2.source == FIXED
    assert out["warning_report"] == []
    (e,) = out["change_log"]
    assert e.description.startswith("Fixes a deprecation warning: applymap")
    assert e.evidence_chunk_ids == ["c1"]  # still cited


def test_a_fix_that_changes_the_output_is_reverted():
    bad = parts(FIXED, [run_result("b2", stdout="DIFFERENT"), run_result("b3", "ok")], [ENTRY])
    out = make_dewarn(ON, bad)(base_state())
    assert "blocks" not in out and out["change_log"] if "change_log" in out else True
    assert any(
        "reverted all warning fixes (b2 prints different output)" in w for w in out["warnings"]
    )
    assert [r["block_id"] for r in out["warning_report"]] == ["b2"]


def test_a_fix_that_leaves_the_warning_in_place_is_reverted():
    still = parts(FIXED, [run_result("b2", warnings=[WARN]), run_result("b3", "ok")], [ENTRY])
    out = make_dewarn(ON, still)(base_state())
    assert "blocks" not in out
    assert any("reverted b2 (the warning is still emitted)" in w for w in out["warnings"])


def test_if_another_block_regresses_the_whole_batch_is_undone():
    broke = parts(
        FIXED, [run_result("b2"), RunResult(block_id="b3", exit_code=1, stdout="")], [ENTRY]
    )
    out = make_dewarn(ON, broke)(base_state())
    assert "blocks" not in out
    assert any("b3 no longer runs" in w for w in out["warnings"])


def test_flag_off_and_no_warnings():
    assert make_dewarn(PipelineFlags(), {})(base_state()) == {}
    clean = base_state().model_copy(update={"run_results": [run_result("b2"), run_result("b3")]})
    assert make_dewarn(ON, {})(clean) == {"warning_report": []}


def test_warnings_that_name_no_symbol_are_only_reported():
    odd = {"category": "FutureWarning", "message": "Something else is deprecated", "line": "1"}
    st = base_state().model_copy(update={"run_results": [run_result("b2", warnings=[odd])]})
    out = make_dewarn(ON, {})(st)  # no stage is ever called
    assert [(w["block_id"], w["kind"]) for w in out["warning_report"]] == [("b2", "deprecation")]


def test_precheck_does_not_protect_a_block_that_warns_when_the_mode_is_on():
    def runner(state, mode, settings):
        res = [run_result("b2", warnings=[WARN]), run_result("b3", "ok")]
        return state.model_copy(update={"run_results": res})

    st = base_state()
    keep = make_precheck(
        PipelineFlags(protect_working_blocks=True), runner=runner, settings=object()
    )
    assert keep(st)["protected_blocks"] == ["b2", "b3"]
    mode = PipelineFlags(protect_working_blocks=True, fix_warnings=True)
    assert make_precheck(mode, runner=runner, settings=object())(st)["protected_blocks"] == ["b3"]


def test_change_log_lists_remaining_warnings_security_first():
    st = base_state().model_copy(
        update={
            "warning_report": [
                {"block_id": "b2", "kind": "deprecation", "category": "FutureWarning", "message": "a"},
                {"block_id": "b3", "kind": "security", "category": "InsecureRequestWarning", "message": "b"},
            ]
        }
    )  # fmt: skip
    md = build_change_log(st, st.blocks)
    assert "## Warnings still emitted" in md
    assert md.index("[SECURITY]") < md.index("[deprecation]")


def test_warned_blocks_groups_by_block():
    res = [run_result("b2", warnings=[WARN, {"category": "UserWarning", "message": "plain"}])]
    assert list(warned_blocks(res)) == ["b2"]
    assert len(warned_blocks(res)["b2"]) == 1  # the plain UserWarning is not handled


@pytest.mark.docker
@needs_docker
def test_blocks_can_write_files_and_use_a_cache_dir_inside_the_sandbox():
    ensure_test_image()
    code = (
        "import os\nopen('note.txt', 'w').write('hi')\nprint(open('note.txt').read())\n"
        "print(os.environ['HOME'], os.environ['MPLCONFIGDIR'], os.getcwd())\n"
    )
    r = run_offline(code, SandboxSettings(image="trf-sandbox:test", timeout_s=20))
    assert r["exit_code"] == 0, r
    assert r["stdout"].split() == ["hi", "/tmp", "/tmp/mpl", "/tmp"]

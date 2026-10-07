"""run_eval orchestration with a fake graph runner: logging, metrics wiring, crash handling."""

import json

from trf.config import PipelineFlags, SandboxSettings
from trf.eval import run_eval as R
from trf.eval.baseline import prepare
from trf.eval.loader import load_case, load_reference
from trf.state import DiffVerdict, GraphState


def fake_runner_factory(use_reference: bool):
    """A 'perfect' pipeline: returns the hand-fixed reference with everything passing."""

    def runner(state0, *, flags, llm):
        ref = prepare(load_reference("pandas_sales_analysis"))
        blocks = [
            b.model_copy(update={"status": "passing"}) if b.lang == "python" else b
            for b in ref.blocks
        ]
        return ref.model_copy(
            update={
                "blocks": blocks,
                "output_md": ref.tutorial_md if use_reference else state0.tutorial_md,
                "verdicts": [
                    DiffVerdict(symbol="pandas.DataFrame.append", verdict="removed",
                                evidence_chunk_ids=["c1"], confidence=0.9)
                ],
                "libraries": ["pandas"],
            }
        )  # fmt: skip

    return runner


def test_configs_match_the_spec_ablation_order():
    assert R.CONFIGS["1_llm_only"].retrieval_enabled is False
    assert R.CONFIGS["2_dense"].use_dense and not R.CONFIGS["2_dense"].use_bm25
    c3 = R.CONFIGS["3_hybrid"]
    assert c3.use_dense and c3.use_bm25 and not c3.use_rerank
    c4 = R.CONFIGS["4_full"]
    assert c4.use_rerank and c4.use_version_boost and c4.use_symbol_index
    assert not c4.use_sandbox_loop and R.CONFIGS["5_loop"].use_sandbox_loop
    assert all(not R.CONFIGS[c].use_sandbox_loop for c in ("1_llm_only", "2_dense", "3_hybrid"))


def test_run_one_logs_metrics_and_inputs():
    log, out_md, _ = R.run_one(
        "pandas_sales_analysis", "4_full", llm=None, sandbox_settings=SandboxSettings(),
        runner=fake_runner_factory(True),
    )  # fmt: skip
    m = log["metrics"]
    assert (m["blocks_passing"], m["blocks_total"]) == (6, 6) and m["tutorial_pass"]
    assert m["minimal_edit"]["unrelated_lines"] == 0  # identical to the reference
    assert m["minimal_edit"]["prose_changed_segments"] == 0
    assert m["diff"] is not None and log["verdicts"][0]["symbol"] == "pandas.DataFrame.append"
    assert log["config"] == "4_full" and log["flags"]["use_rerank"] is True and log["error"] is None
    assert "df.append" not in out_md


def test_llm_only_config_has_no_diff_metrics():
    log, *_ = R.run_one(
        "pandas_sales_analysis", "1_llm_only", llm=None, sandbox_settings=SandboxSettings(),
        runner=fake_runner_factory(True),
    )  # fmt: skip
    assert log["metrics"]["diff"] is None


def test_a_crashing_run_is_recorded_not_raised():
    def boom(state0, *, flags, llm):
        raise RuntimeError("model unreachable")

    log, out_md, _ = R.run_one(
        "pandas_sales_analysis",
        "2_dense",
        llm=None,
        sandbox_settings=SandboxSettings(),
        runner=boom,
    )
    assert log["error"] == "RuntimeError: model unreachable"
    m = log["metrics"]
    assert m["blocks_passing"] == 0 and m["blocks_total"] == 6 and m["unresolved"] == 6
    assert out_md == load_case("pandas_sales_analysis").tutorial_md  # original returned untouched


def test_aggregate_and_markdown(tmp_path):
    good, *_ = R.run_one("pandas_sales_analysis", "4_full", llm=None,
                         sandbox_settings=SandboxSettings(), runner=fake_runner_factory(True))  # fmt: skip
    bad, *_ = R.run_one("pandas_cleaning_ops", "4_full", llm=None,
                        sandbox_settings=SandboxSettings(),
                        runner=lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))  # fmt: skip
    a = R.aggregate([good, bad])
    assert a["cases"] == 2 and a["crashed"] == 1
    assert round(a["block_pass_rate"], 2) == round(6 / 12, 2)
    assert a["tutorial_pass_rate"] == 0.5
    md = R.results_markdown({"4_full": [good, bad]})
    assert "| 4_full | 2 |" in md and "pandas_sales_analysis | 6/6" in md and "6/6 !" not in md
    assert "0/6 !" in md  # the crashed one is flagged


def test_write_results_roundtrip_and_resume_layout(tmp_path):
    log, out_md, change_md = R.run_one(
        "pandas_sales_analysis", "4_full", llm=None, sandbox_settings=SandboxSettings(),
        runner=fake_runner_factory(True),
    )  # fmt: skip
    d = tmp_path / "4_full"
    d.mkdir()
    (d / "pandas_sales_analysis.json").write_text(json.dumps(log), encoding="utf-8")
    R.write_results(tmp_path)
    assert (tmp_path / "results.md").exists()
    assert json.loads((tmp_path / "results.json").read_text())["4_full"]["cases"] == 1
    assert isinstance(R.load_logs(tmp_path)["4_full"][0], dict)
    assert GraphState and PipelineFlags  # imported for completeness

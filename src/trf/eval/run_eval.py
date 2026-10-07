"""Run the ablation configs over the dataset and write a results table.

    uv run python -m trf.eval.run_eval                       # configs 0-4, all 15 cases
    uv run python -m trf.eval.run_eval --configs 4_full --cases pandas_groupby_agg --force

Configs (retrieval stages are added cumulatively; the sandbox always runs once at the end to MEASURE
the result, `5_loop` additionally feeds failures back):
    0_original   no pipeline: the untouched tutorial in the target env (how broken is it?)
    1_llm_only   LLM rewrites every block from its own knowledge, no retrieval, no verdicts
    2_dense      + dense retrieval, diff verdicts with citations
    3_hybrid     + BM25, fused with RRF
    4_full       + rerank, version boost, symbol index
    5_loop       + sandbox feedback loop

Per run it logs inputs, retrieved chunk ids, verdicts, change log, run results and metrics as JSON
under <out>/<config>/<case>.json (plus the output Markdown and change log). Existing logs are
reused unless --force, so an interrupted eval resumes where it stopped.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from trf.config import PipelineFlags, SandboxSettings
from trf.eval import metrics as M
from trf.eval.baseline import prepare, run_state
from trf.eval.loader import (
    DATASET_DIR,
    list_cases,
    load_case,
    load_expected_verdicts,
)
from trf.graph import run_graph
from trf.llm import LLM
from trf.state import GraphState

OFF = {f: False for f in PipelineFlags.model_fields}
CONFIGS: dict[str, PipelineFlags] = {
    "1_llm_only": PipelineFlags(**OFF),
    "2_dense": PipelineFlags(**{**OFF, "use_dense": True}),
    "3_hybrid": PipelineFlags(**{**OFF, "use_dense": True, "use_bm25": True}),
    "4_full": PipelineFlags(
        **{
            **OFF,
            "use_dense": True,
            "use_bm25": True,
            "use_rerank": True,
            "use_version_boost": True,
            "use_symbol_index": True,
        }  # fmt: skip
    ),
    "5_loop": PipelineFlags(
        **{
            **OFF,
            "use_dense": True,
            "use_bm25": True,
            "use_rerank": True,
            "use_version_boost": True,
            "use_symbol_index": True,
            "use_sandbox_loop": True,
        }  # fmt: skip
    ),
}
DEFAULT_CONFIGS = ["0_original", "1_llm_only", "2_dense", "3_hybrid", "4_full"]

GraphRunner = Callable[..., GraphState]


def run_original(state0: GraphState, settings: SandboxSettings) -> GraphState:
    """Config 0: execute the untouched tutorial in the target env."""
    s = run_state(prepare(state0), "target", settings)
    return s.model_copy(update={"output_md": state0.tutorial_md})


def _compact_runs(s: GraphState) -> list[dict]:
    return [
        {
            "block_id": r.block_id,
            "ok": r.ok,
            "exit_code": r.exit_code,
            "stdout": r.stdout[:500],
            "traceback": (r.traceback or "")[-800:],
            "passed_assertions": r.passed_assertions,
            "timed_out": r.timed_out,
        }  # fmt: skip
        for r in s.run_results
    ]


def run_one(
    case: str,
    cfg_name: str,
    *,
    llm: LLM | None,
    sandbox_settings: SandboxSettings,
    runner: GraphRunner = run_graph,
    max_loops: int = 4,
    dataset_dir: Path = DATASET_DIR,
) -> tuple[dict, str, str]:
    """Returns (log, output_md, change_log_md)."""
    state0 = load_case(case, dataset_dir).model_copy(update={"max_loops": max_loops})
    expected = load_expected_verdicts(case, dataset_dir)
    reference_md = (dataset_dir / f"{case}.fixed.md").read_text(encoding="utf-8")
    flags = CONFIGS.get(cfg_name)
    calls0, t0 = (llm.calls if llm else 0), time.time()
    error = None
    try:
        final = (
            run_original(state0, sandbox_settings)
            if cfg_name == "0_original"
            else runner(state0, flags=flags, llm=llm)
        )
    except Exception as e:  # one broken case must not abort the whole eval
        error = f"{type(e).__name__}: {e}"
        base = prepare(state0)
        final = base.model_copy(
            update={
                "blocks": [
                    b.model_copy(update={"status": "unresolved", "last_error": error})
                    if b.lang == "python"
                    else b
                    for b in base.blocks
                ],  # fmt: skip
                "output_md": state0.tutorial_md,
                "warnings": [f"run crashed: {error}", traceback.format_exc()[-1500:]],
            }
        )
    seconds = round(time.time() - t0, 1)

    passed, total = M.pass_counts(final)
    has_diff = flags is not None and flags.retrieval_enabled
    log = {
        "case": case, "config": cfg_name, "flags": flags.model_dump() if flags else None,
        "error": error, "seconds": seconds, "llm_calls": (llm.calls - calls0) if llm else 0,
        "inputs": {
            "libraries": final.libraries, "old_versions": final.old_versions,
            "target_versions": final.target_versions, "pins": final.pins,
            "n_symbols": len(final.symbols),
        },
        "retrieved": {sym: [c.id for c in cs] for sym, cs in final.evidence.items()},
        "verdicts": [v.model_dump() for v in final.verdicts],
        "change_log": [e.model_dump() for e in final.change_log],
        "blocks": [
            {"id": b.id, "lang": b.lang, "status": b.status, "last_error": b.last_error}
            for b in final.blocks
        ],
        "run_results": _compact_runs(final),
        "warnings": final.warnings,
        "loops": final.loop_count,
        "metrics": {
            "blocks_passing": passed, "blocks_total": total,
            "tutorial_pass": M.tutorial_passes(final),
            "unresolved": M.unresolved_count(final)[0],
            "diff": M.diff_accuracy(final, expected) if has_diff else None,
            "minimal_edit": M.minimal_edit(state0.tutorial_md, final.output_md, reference_md),
        },
    }  # fmt: skip
    return log, final.output_md, final.change_log_md


def aggregate(logs: list[dict]) -> dict:
    n = len(logs)
    passed = sum(x["metrics"]["blocks_passing"] for x in logs)
    total = sum(x["metrics"]["blocks_total"] for x in logs)
    diffs = [x["metrics"]["diff"] for x in logs if x["metrics"]["diff"]]
    me = [x["metrics"]["minimal_edit"] for x in logs if x["metrics"]["minimal_edit"]]
    changed_exp = sum(d["changed_expected"] for d in diffs)
    return {
        "cases": n,
        "block_pass_rate": passed / total if total else 0.0,
        "tutorial_pass_rate": sum(x["metrics"]["tutorial_pass"] for x in logs) / n if n else 0.0,
        "diff_accuracy": (sum(d["correct"] for d in diffs) / sum(d["symbols"] for d in diffs))
        if diffs
        else None,
        "changed_recall": (sum(d["changed_hit"] for d in diffs) / changed_exp)
        if diffs and changed_exp
        else None,
        "verdict_exact": (sum(d["verdict_exact"] for d in diffs) / changed_exp)
        if diffs and changed_exp
        else None,
        "unrelated_edit_ratio": (
            sum(m["unrelated_lines"] for m in me) / max(sum(m["code_lines"] for m in me), 1)
        )
        if me
        else None,
        "prose_changed_segments": sum(m["prose_changed_segments"] for m in me) if me else None,
        "unresolved_rate": sum(x["metrics"]["unresolved"] for x in logs) / total if total else 0.0,
        "mean_llm_calls": sum(x["llm_calls"] for x in logs) / n if n else 0.0,
        "mean_seconds": sum(x["seconds"] for x in logs) / n if n else 0.0,
        "crashed": sum(1 for x in logs if x["error"]),
    }


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{100 * v:.0f}%"


def results_markdown(by_config: dict[str, list[dict]]) -> str:
    aggs = {c: aggregate(v) for c, v in by_config.items() if v}
    head = (
        "| config | cases | block pass | tutorial pass | diff acc. | changed recall "
        "| verdict exact | unrelated edits | prose edits | unresolved | LLM calls | s/case |"
    )
    lines = ["# Eval results", "", head, "|" + "---|" * 12]  # fmt: skip
    for c, a in aggs.items():
        prose = a["prose_changed_segments"]
        cells = [
            c,
            str(a["cases"]),
            _pct(a["block_pass_rate"]),
            _pct(a["tutorial_pass_rate"]),
            _pct(a["diff_accuracy"]),
            _pct(a["changed_recall"]),
            _pct(a["verdict_exact"]),
            _pct(a["unrelated_edit_ratio"]),
            "n/a" if prose is None else str(prose),
            _pct(a["unresolved_rate"]),
            f"{a['mean_llm_calls']:.1f}",
            f"{a['mean_seconds']:.0f}",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    cases = sorted({x["case"] for v in by_config.values() for x in v})
    lines += ["", "## Passing python blocks per tutorial", "",
              "| case | " + " | ".join(aggs) + " |", "|---|" + "---|" * len(aggs)]  # fmt: skip
    for case in cases:
        cells = []
        for c in aggs:
            row = next((x for x in by_config[c] if x["case"] == case), None)
            cells.append(
                "-"
                if row is None
                else f"{row['metrics']['blocks_passing']}/{row['metrics']['blocks_total']}"
                + (" !" if row["error"] else "")
            )
        lines.append(f"| {case} | " + " | ".join(cells) + " |")
    lines += ["", "`!` = the run crashed (blocks counted as unresolved). Metric definitions: "
              "see `trf/eval/metrics.py`.", ""]  # fmt: skip
    return "\n".join(lines)


def load_logs(out: Path) -> dict[str, list[dict]]:
    by_config: dict[str, list[dict]] = {}
    for cfg_dir in sorted(p for p in out.iterdir() if p.is_dir()):
        for f in sorted(cfg_dir.glob("*.json")):
            by_config.setdefault(cfg_dir.name, []).append(json.loads(f.read_text(encoding="utf-8")))
    return by_config


def write_results(out: Path) -> None:
    by_config = load_logs(out)
    (out / "results.md").write_text(results_markdown(by_config), encoding="utf-8")
    (out / "results.json").write_text(
        json.dumps({c: aggregate(v) for c, v in by_config.items() if v}, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="*", default=DEFAULT_CONFIGS)
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--out", default="runs/eval")
    ap.add_argument("--force", action="store_true", help="re-run even if a log exists")
    ap.add_argument("--max-loops", type=int, default=4)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sandbox_settings = SandboxSettings.from_env()
    cases = args.cases or list_cases()
    llm = LLM.from_env() if any(c != "0_original" for c in args.configs) else None

    for cfg in args.configs:
        if cfg != "0_original" and cfg not in CONFIGS:
            raise SystemExit(f"unknown config {cfg!r}; choose from {['0_original', *CONFIGS]}")
        (out / cfg).mkdir(parents=True, exist_ok=True)
        for case in cases:
            target = out / cfg / f"{case}.json"
            if target.exists() and not args.force:
                print(f"[skip] {cfg}/{case} (log exists)")
                continue
            log, out_md, change_md = run_one(
                case, cfg, llm=llm, sandbox_settings=sandbox_settings, max_loops=args.max_loops
            )
            target.write_text(json.dumps(log, indent=2), encoding="utf-8")
            (out / cfg / f"{case}.out.md").write_text(out_md, encoding="utf-8", newline="\n")
            (out / cfg / f"{case}.changelog.md").write_text(change_md, encoding="utf-8")
            m = log["metrics"]
            print(
                f"[done] {cfg}/{case}: {m['blocks_passing']}/{m['blocks_total']} blocks, "
                f"{log['llm_calls']} LLM calls, {log['seconds']}s"
                + (f"  ERROR {log['error'][:80]}" if log["error"] else "")
            )
            write_results(out)  # keep partial results readable while the eval runs
    write_results(out)
    print((out / "results.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

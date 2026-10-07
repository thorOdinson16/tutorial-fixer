"""Phase 1 baseline: run the ORIGINAL tutorial code in a sandbox and record what breaks.

    uv run python -m trf.eval.baseline                  # target-version env (how broken is it?)
    uv run python -m trf.eval.baseline --mode old       # control: old pinned env (is the tutorial
                                                        # itself valid? should be all green)
Writes one JSON log per case plus summary.json under runs/baseline-<mode>/.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trf.config import SandboxSettings
from trf.eval.loader import list_cases, load_case
from trf.nodes import extract, ingest
from trf.nodes.resolve_versions import resolve_versions
from trf.nodes.sandbox import ensure_env_image, make_sandbox
from trf.state import GraphState

OLD_PYTHON = "3.10"  # old pandas/pydantic wheels exist for 3.10, not 3.12
OLD_EXTRA_REQS = ["numpy<2"]  # pandas 1.x is binary-incompatible with numpy 2


def apply(state: GraphState, node) -> GraphState:
    return state.model_copy(update=node(state))


def run_state(s: GraphState, mode: str, settings: SandboxSettings) -> GraphState:
    """Execute an already ingested/extracted/resolved state in the target or old-pins env."""
    builder = ensure_env_image
    if mode == "old":
        missing = [lib for lib in s.libraries if lib not in s.old_versions]
        if missing:
            raise SystemExit(f"no old version known for {missing}; cannot run control")
        s = s.model_copy(update={"target_versions": dict(s.old_versions)})
        settings = settings.model_copy(update={"python_version": OLD_PYTHON})

        def builder(reqs, optional, st):
            return ensure_env_image([*reqs, *OLD_EXTRA_REQS], optional, st)

    return apply(s, make_sandbox(env_builder=builder, settings=settings))


def prepare(s: GraphState) -> GraphState:
    for node in (ingest.ingest, extract.extract, resolve_versions):
        s = apply(s, node)
    return s


def run_case(name: str, mode: str, settings: SandboxSettings) -> tuple[GraphState, dict]:
    s = run_state(prepare(load_case(name)), mode, settings)
    runs = {r.block_id: r for r in s.run_results}
    blocks = []
    for b in s.blocks:
        r = runs.get(b.id)
        blocks.append(
            {
                "id": b.id,
                "lang": b.lang,
                "status": b.status,
                "last_error": b.last_error,
                "stdout": r.stdout if r else None,
                "traceback": r.traceback if r else None,
                "passed_assertions": r.passed_assertions if r else None,
            }  # fmt: skip
        )
    log = {
        "case": name, "mode": mode, "tutorial_date": str(s.tutorial_date), "pins": s.pins,
        "libraries": s.libraries, "old_versions": s.old_versions,
        "target_versions": s.target_versions, "extra_packages": s.extra_packages,
        "symbols": [x.model_dump() for x in s.symbols], "blocks": blocks,
    }  # fmt: skip
    return s, log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["target", "old"], default="target")
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out = Path(args.out or f"runs/baseline-{args.mode}")
    out.mkdir(parents=True, exist_ok=True)
    settings = SandboxSettings.from_env()
    rows = []
    for name in args.cases or list_cases():
        s, log = run_case(name, args.mode, settings)
        (out / f"{name}.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        py = [b for b in log["blocks"] if b["lang"] == "python"]
        failing = [b for b in py if b["status"] == "failing"]
        rows.append(
            {
                "case": name,
                "python_blocks": len(py),
                "passing": sum(b["status"] == "passing" for b in py),
                "failing": len(failing),
                "first_failure": (failing[0]["id"] + ": " + (failing[0]["last_error"] or ""))
                if failing
                else "",
                "versions": s.target_versions,
            }  # fmt: skip
        )
    (out / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nbaseline ({args.mode}) -> {out}")
    print(f"{'case':32} {'blocks':>6} {'pass':>5} {'fail':>5}  first failure")
    for r in rows:
        head = f"{r['case']:32} {r['python_blocks']:>6} {r['passing']:>5} {r['failing']:>5}"
        print(f"{head}  {r['first_failure'][:90]}")


if __name__ == "__main__":
    main()

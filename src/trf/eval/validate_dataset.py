"""Prove the eval dataset is sound before trusting any score computed against it.

    uv run python -m trf.eval.validate_dataset [--cases a b] [--skip-control] [--skip-reference]
                                               [--dataset-dir DIR]

For every case:
  control    the ORIGINAL tutorial must pass (assertions included) in its original pinned env;
             otherwise the tutorial is itself broken and measures nothing.
  reference  the hand-fixed `<name>.fixed.md` must pass in the target env, and must differ from
             the original only inside code blocks (prose identical).
Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trf.config import SandboxSettings
from trf.eval.baseline import prepare, run_state
from trf.eval.loader import DATASET_DIR, list_cases, load_case, load_reference
from trf.nodes.ingest import split_segments
from trf.state import GraphState


def python_summary(s: GraphState) -> tuple[int, int, list[str]]:
    py = [b for b in s.blocks if b.lang == "python"]
    bad = [f"{b.id}: {b.last_error}" for b in py if b.status != "passing"]
    return len(py), len(py) - len(bad), bad


def prose_identical(a: str, b: str) -> bool:
    prose = lambda md: [s.text for s in split_segments(md) if s.kind == "prose"]  # noqa: E731
    return prose(a) == prose(b)


def structure_matches(a: GraphState, b: GraphState) -> bool:
    return [(x.id, x.lang) for x in a.blocks] == [(x.id, x.lang) for x in b.blocks]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--skip-control", action="store_true")
    ap.add_argument("--skip-reference", action="store_true")
    ap.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    args = ap.parse_args()
    d = args.dataset_dir

    settings = SandboxSettings.from_env()
    failures: list[str] = []
    print(f"{'case':38} {'control':>9} {'reference':>10}  notes")
    for name in args.cases or list_cases(d):
        notes: list[str] = []
        orig = prepare(load_case(name, d))
        ref0 = prepare(load_reference(name, d))

        if not prose_identical(orig.tutorial_md, ref0.tutorial_md):
            notes.append("PROSE DIFFERS")
        if not structure_matches(orig, ref0):
            notes.append("BLOCK STRUCTURE DIFFERS")

        control = reference = "-"
        if not args.skip_control:
            n, ok, bad = python_summary(run_state(orig, "old", settings))
            control = f"{ok}/{n}"
            notes += [f"control {b}" for b in bad]
        if not args.skip_reference:
            n, ok, bad = python_summary(run_state(ref0, "target", settings))
            reference = f"{ok}/{n}"
            notes += [f"reference {b}" for b in bad]
        print(f"{name:38} {control:>9} {reference:>10}  {'; '.join(notes)[:140]}")
        if notes:
            failures.append(name)

    print("\nOK: dataset validated" if not failures else f"\nFAILED: {failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

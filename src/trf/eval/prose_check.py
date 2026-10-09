"""Check a prose-update case: `NAME.prose_edits.json` must turn the original prose into the fixed one.

    uv run python -m trf.eval.prose_check --dataset-dir src/trf/eval/dataset_prose [--cases a b]

A case has the usual four files plus `NAME.prose_edits.json`:

    {"edits": [{"old": "<exact original text>", "new": "<exact replacement>", "why": "..."}]}

Each `old` must occur exactly once in the original's PROSE (never inside a code fence), applying all
edits to `NAME.md` must give exactly the prose of `NAME.fixed.md`, and the code block structure must
match. This is what makes the hand-fixed prose a usable ground truth: every changed sentence is
listed, so an unlisted change is a mistake in the reference, not a judgement call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trf.eval.loader import DATASET_DIR, list_cases
from trf.nodes.ingest import split_segments


def prose_segments(md: str) -> list[str]:
    return [s.text for s in split_segments(md) if s.kind == "prose"]


def load_edits(name: str, directory: Path = DATASET_DIR) -> list[dict]:
    f = directory / f"{name}.prose_edits.json"
    return json.loads(f.read_text(encoding="utf-8"))["edits"] if f.exists() else []


def apply_edits(md: str, edits: list[dict]) -> tuple[list[str], list[str]]:
    """(prose segments after the edits, problems). Each `old` must match exactly one place."""
    segs = prose_segments(md)
    problems: list[str] = []
    for e in edits:
        old, new = e.get("old", ""), e.get("new", "")
        hits = [(i, s.count(old)) for i, s in enumerate(segs) if old and old in s]
        n = sum(c for _, c in hits)
        if n != 1:
            problems.append(f"edit matches {n} places (need exactly 1): {old[:60]!r}")
            continue
        if old == new:
            problems.append(f"edit changes nothing: {old[:60]!r}")
            continue
        i = hits[0][0]
        segs[i] = segs[i].replace(old, new, 1)
    return segs, problems


def check_case(name: str, directory: Path) -> list[str]:
    orig = (directory / f"{name}.md").read_text(encoding="utf-8")
    fixed = (directory / f"{name}.fixed.md").read_text(encoding="utf-8")
    edits = load_edits(name, directory)
    if not edits:
        return ["no prose edits listed (a prose case needs at least one)"]
    segs, problems = apply_edits(orig, edits)
    if problems:
        return problems
    if segs != prose_segments(fixed):
        problems.append("applying the listed edits to the original does not give the fixed prose")
    code = lambda md: [s.text for s in split_segments(md) if s.kind == "code"]  # noqa: E731
    if len(code(orig)) != len(code(fixed)):
        problems.append("original and fixed have a different number of code blocks")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    ap.add_argument("--cases", nargs="*", default=None)
    args = ap.parse_args()
    failed = 0
    for name in args.cases or list_cases(args.dataset_dir):
        problems = check_case(name, args.dataset_dir)
        n = len(load_edits(name, args.dataset_dir))
        print(f"{name:34} {n} edits  {'OK' if not problems else 'PROBLEMS'}")
        for p in problems:
            print(f"    - {p}")
        failed += bool(problems)
    print("\nOK: prose edits validated" if not failed else f"\nFAILED: {failed} case(s)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

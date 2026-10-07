"""Eval metrics. All pure functions of finished states / Markdown, so they are unit-testable.

pass rate         primary. Share of python blocks that ran green (assertions included), plus the
                  stricter share of tutorials where every python block is green.
diff accuracy     symbol-level: predicted verdict vs hand label. A symbol with no recorded verdict
                  is predicted `unchanged`; unlabeled symbols are expected `unchanged`.
minimal edit      lines of the ORIGINAL code the system changed that the hand-fixed reference did
                  NOT change (unrelated churn), over all original code lines; plus the number of
                  prose segments altered (should be 0).
unresolved rate   share of python blocks flagged `unresolved` (honest failure) rather than passing.
"""

from __future__ import annotations

import difflib

from trf.nodes.ingest import split_segments
from trf.state import GraphState

SKIP_KINDS = {"cli", "module"}


def python_blocks(s: GraphState):
    return [b for b in s.blocks if b.lang == "python"]


def pass_counts(s: GraphState) -> tuple[int, int]:
    py = python_blocks(s)
    return sum(b.status == "passing" for b in py), len(py)


def tutorial_passes(s: GraphState) -> bool:
    passed, total = pass_counts(s)
    return total > 0 and passed == total


def unresolved_count(s: GraphState) -> tuple[int, int]:
    py = python_blocks(s)
    return sum(b.status == "unresolved" for b in py), len(py)


def diff_accuracy(s: GraphState, expected: dict[str, str]) -> dict[str, int]:
    """Confusion counts over the tutorial's library symbols (CLI lines excluded)."""
    predicted = {v.symbol: v.verdict for v in s.verdicts}
    syms = [x.name for x in s.symbols if x.kind not in SKIP_KINDS and x.library in s.libraries]
    out = {"symbols": 0, "correct": 0, "changed_expected": 0, "changed_predicted": 0,
           "changed_hit": 0, "verdict_exact": 0}  # fmt: skip
    for name in syms:
        exp, got = expected.get(name, "unchanged"), predicted.get(name, "unchanged")
        out["symbols"] += 1
        out["correct"] += exp == got
        if exp != "unchanged":
            out["changed_expected"] += 1
            out["changed_hit"] += got != "unchanged"
            out["verdict_exact"] += exp == got
        if got != "unchanged":
            out["changed_predicted"] += 1
    return out


def _changed_original_lines(orig: list[str], new: list[str]) -> set[int]:
    changed: set[int] = set()
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, orig, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2:  # pure insertion: attribute it to the neighbouring original line
            changed.add(min(i1, max(len(orig) - 1, 0)))
        changed.update(range(i1, i2))
    return changed


def minimal_edit(
    original_md: str, output_md: str, reference_md: str
) -> dict[str, float | int] | None:
    """None if the three documents do not have the same number of code blocks."""
    o, s, r = (split_segments(x) for x in (original_md, output_md, reference_md))
    code = lambda segs: [x.text for x in segs if x.kind == "code"]  # noqa: E731
    prose = lambda segs: [x.text for x in segs if x.kind == "prose"]  # noqa: E731
    oc, sc, rc = code(o), code(s), code(r)
    if not (len(oc) == len(sc) == len(rc)):
        return None
    unrelated = total = 0
    for a, b, c in zip(oc, sc, rc, strict=True):
        ol = a.splitlines()
        total += len(ol)
        unrelated += len(
            _changed_original_lines(ol, b.splitlines())
            - _changed_original_lines(ol, c.splitlines())
        )
    po, ps = prose(o), prose(s)
    prose_changed = abs(len(po) - len(ps)) + sum(x != y for x, y in zip(po, ps, strict=False))
    return {
        "unrelated_lines": unrelated,
        "code_lines": total,
        "ratio": unrelated / total if total else 0.0,
        "prose_changed_segments": prose_changed,
    }

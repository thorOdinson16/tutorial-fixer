"""Build the defect-repair eval set by injecting a missing import into validated tutorials.

    uv run python scripts/make_defect_set.py

For each chosen base case the first `import numpy as np` / `import pandas as pd` /
`import matplotlib.pyplot as plt` line is deleted from the first Python block that has it (only if
no other block imports the same alias, so the deletion really leaves the name undefined).

  def_pure_<case>   the hand-fixed REFERENCE minus that import: no version rot at all, one defect.
  def_mixed_<case>  the ORIGINAL (rotten) tutorial minus that import: version rot AND the defect.

Both keep the base case's assertions; the reference of both is the base `.fixed.md`, which already
passes in the target environment. Deterministic: same inputs, same output.
"""

from __future__ import annotations

import json
import re
import shutil

from trf.eval.loader import DATASET_DIR, list_cases
from trf.nodes.ingest import split_segments

ROOT = DATASET_DIR.parent
SOURCES = [DATASET_DIR, ROOT / "dataset_heldout", ROOT / "dataset_libs"]
OUT = ROOT / "dataset_defects"
ALIASES = {"np": "numpy", "pd": "pandas", "plt": "matplotlib.pyplot"}
IMPORT = re.compile(r"^import ([\w.]+) as (np|pd|plt)[ \t]*\n", re.M)


def inject(md: str) -> tuple[str, str] | None:
    """(defective markdown, the removed line) or None if no import can be removed safely."""
    segs = split_segments(md)
    code = [s for s in segs if s.kind == "code"]
    for seg in code:
        m = IMPORT.search(seg.text)
        if not m or ALIASES[m[2]] != m[1]:
            continue
        alias_pat = re.compile(rf"^import [\w.]+ as {m[2]}\b", re.M)
        if sum(len(alias_pat.findall(c.text)) for c in code) != 1:
            continue  # imported again elsewhere: removing it would not leave the name undefined
        new_text = seg.text[: m.start()] + seg.text[m.end() :]
        if not new_text.strip():
            continue
        out = "".join(
            (s.fence_open + new_text + s.fence_close) if s is seg else
            (s.fence_open + s.text + s.fence_close if s.kind == "code" else s.text)
            for s in segs
        )  # fmt: skip
        return out, m.group(0).strip()
    return None


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    made = 0
    want = {"pure": 0, "mixed": 0}
    candidates = []
    for d in SOURCES:
        for name in list_cases(d):
            candidates.append((d, name))
    for k, (d, name) in enumerate(sorted(candidates, key=lambda t: t[1])):
        kind = "pure" if k % 2 == 0 else "mixed"
        if want[kind] >= 6:
            kind = "mixed" if kind == "pure" else "pure"
        if want[kind] >= 6:
            continue
        orig = (d / f"{name}.md").read_text(encoding="utf-8")
        ref = (d / f"{name}.fixed.md").read_text(encoding="utf-8")
        got = inject(ref if kind == "pure" else orig)
        if got is None:
            continue
        defective, removed = got
        stem = f"def_{kind}_{name}"
        (OUT / f"{stem}.md").write_text(defective, encoding="utf-8", newline="\n")
        shutil.copy(d / f"{name}.fixed.md", OUT / f"{stem}.fixed.md")
        shutil.copy(d / f"{name}.assertions.json", OUT / f"{stem}.assertions.json")
        symbols = (
            (d / f"{name}.symbols.json").read_text(encoding="utf-8") if kind == "mixed" else "{}"
        )
        (OUT / f"{stem}.symbols.json").write_text(symbols, encoding="utf-8")
        (OUT / f"{stem}.defect.json").write_text(
            json.dumps({"removed": removed, "base": name, "kind": kind}), encoding="utf-8"
        )
        want[kind] += 1
        made += 1
        print(f"{stem}: removed `{removed}`")
    print(f"{made} cases -> {OUT}")


if __name__ == "__main__":
    main()

"""Show how an eval case behaves: original in its old env, original in the target env, reference.

    uv run python scripts/check_case.py CASE... [--dataset-dir DIR]

For a sound case: the original passes in the old env, the reference passes in the target env, and
several blocks of the original FAIL in the target env (that failure is the version rot the case
is meant to contain). Needs Docker.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from trf.config import SandboxSettings
from trf.eval.baseline import prepare, run_state
from trf.eval.loader import DATASET_DIR, load_case, load_reference


def line(label: str, state) -> str:
    py = [b for b in state.blocks if b.lang == "python"]
    cells = " ".join(f"{b.id}:{'ok' if b.status == 'passing' else 'FAIL'}" for b in py)
    return f"  {label:<22} {sum(b.status == 'passing' for b in py)}/{len(py)}  {cells}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    args = ap.parse_args()
    settings = SandboxSettings.from_env()
    for name in args.cases:
        print(name)
        orig = prepare(load_case(name, args.dataset_dir))
        old = run_state(orig, "old", settings)
        new = run_state(orig, "target", settings)
        ref = run_state(prepare(load_reference(name, args.dataset_dir)), "target", settings)
        print(line("original, old env", old))
        print(line("original, target env", new))
        print(line("reference, target env", ref))
        for b in new.blocks:
            if b.lang == "python" and b.status != "passing":
                print(f"    {b.id}: {(b.last_error or '')[:110]}")


if __name__ == "__main__":
    main()

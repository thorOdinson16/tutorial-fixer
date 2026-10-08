"""Run the whole pipeline on one tutorial and print what it did.

    uv run python scripts/demo.py pandas_sales_analysis          # a dataset tutorial
    uv run python scripts/demo.py path/to/tutorial.md --max-loops 3

Needs a configured model (TRF_LLM_PROVIDER / TRF_LLM_MODEL / key), Docker running, and the corpus
built once with `uv run python -m trf.rag.corpus build`. Writes `<name>.out.md` and
`<name>.changelog.md` to `--out` (default runs/demo) and prints a per-block summary. Nothing is
reported as passing unless the sandbox ran it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from trf.eval.loader import DATASET_DIR, list_cases, load_case
from trf.eval.run_eval import CONFIGS
from trf.graph import run_graph
from trf.llm import LLM
from trf.state import GraphState


def load(target: str) -> tuple[str, GraphState]:
    path = Path(target)
    if path.suffix == ".md" and path.exists():
        return path.stem, GraphState(tutorial_md=path.read_text(encoding="utf-8"))
    if target in list_cases():
        return target, load_case(target, DATASET_DIR)
    raise SystemExit(f"{target!r} is neither a .md file nor a dataset case: {list_cases()}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help="a dataset case name or a path to a Markdown tutorial")
    ap.add_argument("--config", default="5_loop", choices=sorted(CONFIGS))
    ap.add_argument("--max-loops", type=int, default=4)
    ap.add_argument(
        "--protect",
        action="store_true",
        help="never edit a block that already runs (runs the original in old and new envs first)",
    )
    ap.add_argument("--out", default="runs/demo")
    args = ap.parse_args()

    name, state = load(args.target)
    state = state.model_copy(update={"max_loops": args.max_loops})
    llm = LLM.from_env()
    t0 = time.time()
    flags = CONFIGS[args.config]
    if args.protect:
        flags = flags.model_copy(update={"protect_working_blocks": True})
    final = run_graph(state, flags=flags, llm=llm)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.out.md").write_text(final.output_md, encoding="utf-8", newline="\n")
    (out / f"{name}.changelog.md").write_text(final.change_log_md, encoding="utf-8")

    py = [b for b in final.blocks if b.lang == "python"]
    print(f"{name} [{args.config}] in {time.time() - t0:.0f}s, {final.loop_count} sandbox run(s)")
    for lib, ver in sorted(final.target_versions.items()):
        print(f"  {lib}: {final.old_versions.get(lib, '?')} -> {ver}")
    for b in py:
        edited = "edited" if b.source != b.original_source else "unchanged"
        note = f"  ({b.last_error[:90]})" if b.last_error else ""
        print(f"  {b.id}: {b.status:<10} {edited}{note}")
    print(f"{sum(b.status == 'passing' for b in py)}/{len(py)} python blocks passing")
    if llm.usage():
        print("billed tokens:", llm.usage())
    if final.unresolved_block_ids:
        print(f"UNRESOLVED (needs manual attention): {', '.join(final.unresolved_block_ids)}")
    print(f"wrote {out / (name + '.out.md')} and {out / (name + '.changelog.md')}")
    sys.exit(1 if final.unresolved_block_ids else 0)


if __name__ == "__main__":
    main()

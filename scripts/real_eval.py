"""Run the whole pipeline, every mode on, over a folder of tutorials and report before / after.

    uv run python scripts/real_eval.py runs/real3/md --out runs/real3/out [--only name ...]

For each `*.md`: run the ORIGINAL in the target environment (the "as-is" count), then the full
pipeline (version fixes, keep-working-blocks, defect repair, prose updates, warning fixes), and write

  <out>/<name>.out.md, <name>.changelog.md, <name>.json     (per tutorial)
  <out>/results.md, results.json                            (summary)

No hand-written references exist for real pages, so "passing" means the block ran; the summary also
counts edits by kind, prose edits, warning blocks before and after, and what was billed.
Resumable (skips tutorials that already have a JSON log); a spent budget stops the run cleanly.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

from trf.config import SandboxSettings
from trf.eval.baseline import prepare, run_state
from trf.eval.run_eval import CONFIGS
from trf.graph import run_graph
from trf.llm import LLM, BudgetExceeded
from trf.nodes.dewarn import warned_blocks
from trf.state import GraphState


def counts(s: GraphState) -> tuple[int, int]:
    py = [b for b in s.blocks if b.lang == "python"]
    return sum(b.status == "passing" for b in py), len(py)


def run_one(path: Path, out: Path, llm: LLM, settings: SandboxSettings) -> dict:
    md = path.read_text(encoding="utf-8")
    t0 = time.time()
    log: dict = {"name": path.stem, "error": None}
    try:
        base = prepare(GraphState(tutorial_md=md))
        log["libraries"] = base.libraries
        log["old_versions"], log["target_versions"] = base.old_versions, base.target_versions
        n_py = sum(b.lang == "python" for b in base.blocks)
        if n_py == 0 or not base.libraries:
            log.update(skipped="no python blocks" if n_py == 0 else "no supported library",
                       blocks=n_py)  # fmt: skip
            return log
        asis = run_state(base, "target", settings)
        log["asis_pass"], log["blocks"] = counts(asis)
        log["asis_warned"] = len(warned_blocks(asis.run_results))
        u0 = llm.usage()
        flags = CONFIGS["11_everything"]
        final = run_graph(GraphState(tutorial_md=md, max_loops=4), flags=flags, llm=llm)
        log["final_pass"], _ = counts(final)
        log["final_warned"] = len(warned_blocks(final.run_results))
        kinds: dict[str, int] = {}
        for e in final.change_log:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        log["edits"] = kinds
        log["prose_edits"] = len(final.prose_edits)
        log["unresolved"] = final.unresolved_block_ids
        log["suggestions"] = len(final.suggestions)
        log["tokens"] = {k: v - u0.get(k, 0) for k, v in llm.usage().items()}
        (out / f"{path.stem}.out.md").write_text(final.output_md, encoding="utf-8", newline="\n")
        (out / f"{path.stem}.changelog.md").write_text(final.change_log_md, encoding="utf-8")
    except BudgetExceeded:
        raise
    except Exception as e:  # one broken page must not stop the batch
        log["error"] = f"{type(e).__name__}: {e}"
        log["trace"] = traceback.format_exc()[-800:]
    log["seconds"] = round(time.time() - t0)
    return log


def summary(logs: list[dict]) -> str:
    ran = [x for x in logs if "final_pass" in x]
    lines = [
        "# Real tutorials",
        "",
        "| tutorial | libs | blocks | as-is | after | edits | prose | warn before/after |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for x in logs:
        if "final_pass" in x:
            e = sum(x["edits"].values())
            lines.append(
                f"| {x['name'][:48]} | {','.join(x['libraries'])} | {x['blocks']} | {x['asis_pass']} "
                f"| {x['final_pass']} | {e} | {x['prose_edits']} | {x['asis_warned']}/{x['final_warned']} |"
            )
        else:
            why = x.get("skipped") or x.get("error") or "?"
            lines.append(f"| {x['name'][:48]} | - | - | - | - | - | - | not run: {why} |")
    tot = lambda k: sum(x[k] for x in ran)  # noqa: E731
    if ran:
        lines += [
            "",
            f"{len(ran)} tutorials run, {tot('blocks')} python blocks: **{tot('asis_pass')} run as-is,"
            f" {tot('final_pass')} after** the pipeline. Prose edits: {tot('prose_edits')}.",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    llm, settings = LLM.from_env(), SandboxSettings.from_env()
    logs: list[dict] = []
    for path in sorted(args.folder.glob("*.md")):
        if args.only and not any(o in path.stem for o in args.only):
            continue
        f = args.out / f"{path.stem}.json"
        if f.exists():
            logs.append(json.loads(f.read_text(encoding="utf-8")))
            print(f"[skip] {path.stem}")
            continue
        try:
            log = run_one(path, args.out, llm, settings)
        except BudgetExceeded as e:
            print(f"[STOP] {e}")
            break
        f.write_text(json.dumps(log, indent=2), encoding="utf-8")
        logs.append(log)
        print(f"[done] {path.stem}: {log.get('asis_pass', '-')} -> {log.get('final_pass', '-')} "
              f"of {log.get('blocks', '-')} {log.get('skipped', '') or log.get('error') or ''}")  # fmt: skip
        (args.out / "results.md").write_text(summary(logs), encoding="utf-8")
    (args.out / "results.json").write_text(json.dumps(logs, indent=2), encoding="utf-8")
    (args.out / "results.md").write_text(summary(logs), encoding="utf-8")
    print(summary(logs))


if __name__ == "__main__":
    main()

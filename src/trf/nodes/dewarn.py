"""Fix code that still runs but emits deprecation (or security) warnings (opt-in: `fix_warnings`).

The sandbox records every warning a block emits. This stage treats a warned block as a SOFT
failure and reuses the normal machinery on it:

  1. tie each warning to a library symbol the block really uses (`applymap` in "DataFrame.applymap
     has been deprecated");
  2. retrieve release notes for that symbol, with the warning text as a second query;
  3. diff and rewrite exactly as for a failing block, so every change is cited;
  4. re-run the sandbox and keep the edit only if (a) the block still runs, (b) it prints exactly
     what it printed before, (c) that warning is gone, and (d) no other block regressed.
     Otherwise the whole batch is reverted.

Warnings that cannot be tied to a symbol, or whose fix does not verify, stay in the change log's
"Warnings still emitted" section. Security-flavoured warnings (insecure/unverified requests, ...)
are reported prominently and only changed when a CITED fix verifies the same way; this tool never
guesses at a security fix.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from trf.config import PipelineFlags
from trf.nodes.extract import analyze_blocks
from trf.state import GraphState, RunResult, Symbol

MAX_WARNED_BLOCKS = 12
_DEPRECATION = ("Deprecat", "FutureWarning", "LegacyAPI", "PendingDeprecation")
_SECURITY = re.compile(
    r"insecure|unverified|security|vulnerab|verify\s*=\s*False|certificate", re.I
)


def classify(w: dict[str, str]) -> str | None:
    """'security', 'deprecation' or None (not something this stage handles)."""
    cat, msg = w.get("category", ""), w.get("message", "")
    if _SECURITY.search(msg) or "Insecure" in cat or "Security" in cat:
        return "security"
    if any(x in cat for x in _DEPRECATION) or re.search(r"\bdeprecated\b", msg, re.I):
        return "deprecation"
    return None


def warned_blocks(results: list[RunResult]) -> dict[str, list[tuple[str, dict[str, str]]]]:
    """block id -> [(kind, warning)] for the warnings this stage handles."""
    out: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for r in results:
        for w in r.warnings:
            kind = classify(w)
            if kind:
                out.setdefault(r.block_id, []).append((kind, w))
    return out


def _tail(symbol: str) -> str:
    return re.sub(r"\(.*\)$", "", symbol).split(".")[-1].lstrip("@")


def symbols_for_warning(message: str, used: set[str]) -> list[str]:
    """The block's own symbols a warning is about: their last name or keyword is in the message."""
    hits: list[tuple[int, str]] = []
    for sym in sorted(used):
        names = {_tail(sym)}
        arg = re.search(r"\((\w+)\)$", sym)
        if arg:
            names.add(arg[1])
        for n in names:
            if len(n) >= 3 and re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", message):
                hits.append((-len(n), sym))
                break
    names = [s for _, s in sorted(hits)]
    # `pandas.DataFrame` is only the container of `pandas.DataFrame.applymap`: keep the specific one
    names = [s for s in names if not any(o.startswith(s + ".") for o in names)]
    return names[:3]


def _key(w: dict[str, str]) -> tuple[str, str]:
    return w.get("category", ""), w.get("message", "")


def make_dewarn(
    flags: PipelineFlags | None,
    parts: dict[str, Callable[[GraphState], dict]],
) -> Callable[[GraphState], dict]:
    """`parts` holds the stage callables to reuse: retrieve, diff, rewrite, sandbox."""

    def dewarn(state: GraphState) -> dict:
        f = flags or PipelineFlags()
        if not f.fix_warnings or not state.run_results:
            return {}
        before = {r.block_id: r for r in state.run_results}
        warned = warned_blocks(state.run_results)
        if not warned:
            return {"warning_report": []}
        by_id = {b.id: b for b in state.blocks}
        used = analyze_blocks(state.blocks).used

        symbols = list(state.symbols)
        known = {s.name for s in symbols}
        error_symbols: dict[str, list[str]] = {}
        soft: dict[str, str] = {}
        for bid, ws in warned.items():
            b = by_id.get(bid)
            if b is None or b.lang != "python" or b.status != "passing":
                continue
            names: list[str] = []
            for _, w in ws:
                names += symbols_for_warning(w["message"], used.get(bid, set()))
            if not names:
                continue
            names = list(dict.fromkeys(names))
            error_symbols[bid] = names
            _, first = ws[0]
            soft[bid] = f"{first['category']}: {first['message']}"
            for n in names:
                if n not in known:
                    symbols.append(Symbol(name=n, library=n.split(".")[0], first_seen_in_block=bid))
                    known.add(n)
            if len(soft) >= MAX_WARNED_BLOCKS:
                break
        if not soft:
            return {"warning_report": _report(state.run_results)}

        trial_blocks = [
            b.model_copy(update={"status": "failing", "last_error": soft[b.id]})
            if b.id in soft
            else b
            for b in state.blocks
        ]
        trial = state.model_copy(
            update={
                "blocks": trial_blocks,
                "symbols": symbols,
                "error_symbols": error_symbols,
                "fresh_symbols": sorted({n for ns in error_symbols.values() for n in ns}),
                "loop_count": max(1, state.loop_count),
            }
        )
        for name in ("retrieve", "diff", "rewrite"):
            trial = trial.model_copy(update=parts[name](trial))
        edited = {b.id for b in trial.blocks if b.id in soft and b.source != by_id[b.id].source}
        warnings = list(state.warnings)
        if not edited:
            return {"warnings": warnings, "warning_report": _report(state.run_results)}

        # verify: reset the statuses, run everything again, compare with the earlier run
        verify_blocks = [
            b.model_copy(update={"status": "pending", "last_error": None})
            if b.lang == "python"
            else b
            for b in trial.blocks
        ]
        verify = trial.model_copy(update={"blocks": verify_blocks})
        verify = verify.model_copy(update=parts["sandbox"](verify))
        after = {r.block_id: r for r in verify.run_results}

        def regressed(bid: str) -> str | None:
            was, now = before.get(bid), after.get(bid)
            if was is None or not was.ok:
                return None
            if now is None or not now.ok:
                return "no longer runs"
            if now.stdout.strip() != was.stdout.strip():
                return "prints different output"
            return None

        problems: dict[str, str] = {}
        for b in state.blocks:
            if b.lang != "python":
                continue
            why = regressed(b.id)
            if why:
                problems[b.id] = why
        ok_edits: set[str] = set()
        for bid in edited:
            now = after.get(bid)
            gone = now is not None and not any(
                _key(w) in {_key(x) for _, x in warned[bid]} for w in now.warnings
            )
            if bid not in problems and gone:
                ok_edits.add(bid)
            elif bid not in problems:
                warnings.append(f"dewarn: reverted {bid} (the warning is still emitted)")
        if problems:  # something else broke: undo the whole batch
            for bid, why in problems.items():
                warnings.append(f"dewarn: reverted all warning fixes ({bid} {why})")
            ok_edits = set()
        if not ok_edits:
            return {"warnings": warnings, "warning_report": _report(state.run_results)}

        if ok_edits == edited:
            final = verify  # every edit held up: the verification run IS the final run
        else:  # some edits were reverted, so verify the code that is actually kept
            kept = [
                by_id[b.id] if b.id in edited and b.id not in ok_edits else b for b in verify_blocks
            ]
            final = verify.model_copy(
                update={
                    "blocks": [
                        b.model_copy(update={"status": "pending", "last_error": None})
                        if b.lang == "python"
                        else b
                        for b in kept
                    ]
                }
            )
            final = final.model_copy(update=parts["sandbox"](final))
        new_entries = [
            e.model_copy(update={"description": f"Fixes a deprecation warning: {e.description}"})
            for e in trial.change_log
            if e not in state.change_log and e.block_id in ok_edits
        ]
        return {
            "blocks": final.blocks,
            "run_results": final.run_results,
            "change_log": [*state.change_log, *new_entries],
            "warnings": warnings,
            "warning_report": _report(final.run_results),
        }

    return dewarn


def _report(results: list[RunResult]) -> list[dict[str, str]]:
    return [
        {"block_id": bid, "kind": kind, "category": w["category"], "message": w["message"]}
        for bid, ws in warned_blocks(results).items()
        for kind, w in ws
    ]

"""Fix blocks that fail for reasons that are not library versions (opt-in: `repair_defects`).

Real tutorials are often incomplete: a snippet uses `np` or `List` that nothing imports, or needs
a package the tutorial never installs. After the version-fixing loop ends, this stage repairs what
can be repaired SAFELY and verifies every repair by running the sandbox again:

  * missing import (NameError for a well-known name): add the import line at the top of the failing
    block;
  * missing package (ModuleNotFoundError for an installable, non-local module, or pandas' "Missing
    optional dependency"): add it to the install line and the sandbox environment;
  * undefined name (anything else): the LLM drafts a minimal definition. It is only a SUGGESTION in
    the change log unless `apply_placeholders` is on, in which case it is inserted with a
    `# added by TRF (assumed): ...` comment and verified like the rest.

A repair is kept only if the error it targets is gone after the sandbox re-run; otherwise it is
reverted. Nothing is invented silently: every repair is a `defect_fix` change-log entry that quotes
the error it removed, and a repair never touches a block that already runs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from trf.config import PipelineFlags
from trf.llm import LLM, LLMError
from trf.nodes.diagnose import COMMON_IMPORTS
from trf.nodes.extract import packages_for_imports
from trf.nodes.rewrite import add_packages_to_install, parses
from trf.nodes.sandbox import sandbox as default_sandbox
from trf.state import ChangeLogEntry, CodeBlock, GraphState

_DOWNSTREAM = "downstream effect"
MAX_CONTEXT_CHARS = 3000


@dataclass
class Repair:
    block_id: str
    kind: str  # import | package | placeholder
    text: str  # the line to insert, or the package name
    error: str
    description: str
    detail: str = ""  # the name or module this repair addresses (to check the error is gone)


class Definition(BaseModel):
    definition: str
    assumption: str = ""


SYSTEM = (
    "You repair incomplete tutorial snippets. A code block uses a name that nothing earlier "
    "defines. "
    "Write the SMALLEST definition (1-4 lines, no prose) that makes the block run, using only the "
    "standard library and libraries the tutorial already imports. Do not change existing code."
)


def _name_error(error: str) -> str | None:
    m = re.search(r"NameError: name '([\w.]+)' is not defined", error)
    return m.group(1) if m else None


def _already_imported(name: str, blocks: list[CodeBlock]) -> bool:
    """True if some block imports `name` (then the NameError has another cause)."""
    pat = re.compile(rf"\b(?:import\s+[\w.]+\s+as\s+{re.escape(name)}|import\s+{re.escape(name)}\b|"
                     rf"from\s+[\w.]+\s+import\s+[^\n]*\b{re.escape(name)}\b)")  # fmt: skip
    return any(pat.search(b.source) for b in blocks if b.lang == "python")


def plan_repairs(state: GraphState) -> list[Repair]:
    """Deterministic repairs for the failing blocks' root-cause errors (no LLM, no side effects)."""
    out: list[Repair] = []
    seen: set[str] = set()
    for b in state.blocks:
        if b.lang != "python" or b.status != "failing" or not b.last_error:
            continue
        err = b.last_error
        if _DOWNSTREAM in err:  # a cascade: the root block is repaired first, then we look again
            continue
        name = _name_error(err)
        if name and name in COMMON_IMPORTS and name not in seen:
            if not _already_imported(name, state.blocks):
                line = COMMON_IMPORTS[name]
                out.append(
                    Repair(
                        b.id,
                        "import",
                        line,
                        err,
                        f"Added `{line}`: the block uses `{name}` but nothing imports it.",
                        detail=name,
                    )  # fmt: skip
                )
                seen.add(name)
            continue
        m = re.search(r"No module named '([\w.]+)'", err) or re.search(
            r"Missing optional dependency '([\w.-]+)'", err
        )
        if m:
            root = m.group(1).split(".")[0]
            pkgs = packages_for_imports({root})
            if pkgs and pkgs[0] not in seen and pkgs[0] not in state.extra_packages:
                out.append(
                    Repair(
                        b.id,
                        "package",
                        pkgs[0],
                        err,
                        f"Added the package `{pkgs[0]}` to the install line and the environment.",
                        detail=root,
                    )  # fmt: skip
                )
                seen.add(pkgs[0])
    return out


def _context(state: GraphState, block: CodeBlock) -> str:
    before = []
    for b in state.blocks:
        if b.id == block.id:
            break
        if b.lang == "python":
            before.append(b.source)
    return "\n".join(before)[-MAX_CONTEXT_CHARS:]


def draft_definition(llm: LLM, state: GraphState, block: CodeBlock, name: str) -> Definition | None:
    prompt = (
        "EARLIER CODE (already run, in the same session):\n"
        f"{_context(state, block) or '(none)'}\n\n"
        f"THIS BLOCK:\n{block.source}\n\nIt fails with: {block.last_error}\n"
        f'Return JSON {{"definition": <python code defining `{name}` (and anything else it '
        'needs, at most 4 lines)>, "assumption": <one short sentence on what you assumed>}}.'
    )
    try:
        d = llm.structured(prompt, Definition, system=SYSTEM)
    except LLMError:
        return None
    if not d.definition.strip() or parses(d.definition):
        return None
    return d


def _same_defect(repair: Repair, block: CodeBlock) -> bool:
    """Does the block still fail with the error this repair targeted?"""
    err = block.last_error or ""
    if block.status == "passing" or not err:
        return False
    if repair.kind == "import" or repair.kind == "placeholder":
        return _name_error(err) == repair.detail
    return repair.detail in err and ("No module named" in err or "Missing optional" in err)


def make_repair(
    flags: PipelineFlags | None = None,
    llm: LLM | None = None,
    sandbox_fn: Callable[[GraphState], dict] = default_sandbox,
) -> Callable[[GraphState], dict]:
    holder = {"llm": llm}

    def repair(state: GraphState) -> dict:
        f = flags or PipelineFlags()
        if not f.repair_defects:
            return {}
        plans = plan_repairs(state)
        suggestions: dict[str, str] = dict(state.suggestions)

        # undefined names: LLM-drafted definitions (suggested, or applied as labelled placeholders)
        for b in state.blocks:
            if b.lang != "python" or b.status != "failing" or not b.last_error:
                continue
            name = _name_error(b.last_error)
            if (
                not name
                or name in COMMON_IMPORTS
                or _DOWNSTREAM in b.last_error
                or any(p.block_id == b.id for p in plans)
            ):
                continue
            if holder["llm"] is None:
                holder["llm"] = LLM.from_env()
            d = draft_definition(holder["llm"], state, b, name)
            if d is None:
                continue
            note = d.assumption.strip() or f"definition of {name}"
            suggestions[b.id] = f"{d.definition.strip()}  # assumed: {note}"
            if f.apply_placeholders:
                lines = [f"# added by TRF (assumed): {note}", *d.definition.strip().splitlines()]
                plans.append(
                    Repair(b.id, "placeholder", "\n".join(lines), b.last_error,
                           f"Added a placeholder definition of `{name}` ({note}).", detail=name)
                )  # fmt: skip

        if not plans:
            return {"suggestions": suggestions} if suggestions != state.suggestions else {}

        trial_blocks = list(state.blocks)
        extra = list(state.extra_packages)
        by_id = {b.id: i for i, b in enumerate(trial_blocks)}
        for p in plans:
            if p.kind == "package":
                extra.append(p.text)
                for i, b in enumerate(trial_blocks):  # also the visible install line
                    if b.lang not in ("bash", "sh", "shell", "console", "zsh", "text", ""):
                        continue
                    new_src, ok = add_packages_to_install(b.source, [p.text])
                    if ok:
                        trial_blocks[i] = b.model_copy(update={"source": new_src})
                        break
            else:
                i = by_id[p.block_id]
                b = trial_blocks[i]
                trial_blocks[i] = b.model_copy(update={"source": p.text + "\n" + b.source})
        trial = state.model_copy(
            update={"blocks": trial_blocks, "extra_packages": sorted(set(extra))}
        )
        trial = trial.model_copy(update=sandbox_fn(trial))
        after = {b.id: b for b in trial.blocks}

        kept: list[Repair] = []
        reverted: list[Repair] = []
        env_failed = any(
            (b.last_error or "").startswith("environment build failed") for b in trial.blocks
        )
        for p in plans:
            blk = after.get(p.block_id)
            ok = blk is not None and not env_failed and not _same_defect(p, blk)
            (kept if ok else reverted).append(p)

        warnings = list(state.warnings)
        for p in reverted:
            warnings.append(
                f"repair: reverted {p.kind} fix for {p.block_id} (it did not remove the error)"
            )

        if reverted:  # rebuild the code from the repairs that held up, then verify once more
            final_blocks = list(state.blocks)
            final_extra = list(state.extra_packages)
            for p in kept:
                if p.kind == "package":
                    final_extra.append(p.text)
                    for i, b in enumerate(final_blocks):
                        if b.lang in ("bash", "sh", "shell", "console", "zsh", "text", ""):
                            new_src, ok = add_packages_to_install(b.source, [p.text])
                            if ok:
                                final_blocks[i] = b.model_copy(update={"source": new_src})
                                break
                else:
                    i = by_id[p.block_id]
                    final_blocks[i] = final_blocks[i].model_copy(
                        update={"source": p.text + "\n" + final_blocks[i].source}
                    )
            trial = state.model_copy(
                update={"blocks": final_blocks, "extra_packages": sorted(set(final_extra))}
            )
            if kept:
                trial = trial.model_copy(update=sandbox_fn(trial))

        entries = list(state.change_log)
        for p in kept:
            entries.append(
                ChangeLogEntry(
                    block_id=p.block_id,
                    kind="defect_fix",
                    description=p.description,
                    error=p.error[:300],
                )  # fmt: skip
            )
        return {
            "blocks": trial.blocks,
            "run_results": trial.run_results,
            "extra_packages": trial.extra_packages,
            "change_log": entries,
            "suggestions": suggestions,
            "warnings": warnings,
        }

    return repair


repair = make_repair()

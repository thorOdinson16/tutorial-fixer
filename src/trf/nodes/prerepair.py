"""Add imports a tutorial forgot, BEFORE symbols are extracted (opt-in: `repair_defects`).

If `import pandas as pd` is missing, every `pd.` call is invisible to the analyzer: no library
symbols are found, so no version fix is even attempted. This stage reads the code statically and,
for each well-known name (`np`, `pd`, `plt`, `List`, ...) that a block uses but no earlier code
defines or imports, inserts the standard import at the top of the first block that uses it. It is
conservative: a name counts as defined if ANY statement in the same or an earlier block binds it
(import, assignment, def, class, loop or with target, argument).

The later sandbox runs verify the result like any other edit; each insertion is a `defect_fix`
change-log entry.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable

from trf.config import PipelineFlags
from trf.nodes.diagnose import COMMON_IMPORTS
from trf.nodes.extract import strip_magics
from trf.state import ChangeLogEntry, GraphState

IMPORTABLE = {n: line for n, line in COMMON_IMPORTS.items() if n.isidentifier()}
# Names several libraries export: the default import is a GUESS unless the tutorial says otherwise.
AMBIGUOUS = {"Path", "Field", "Enum", "Counter", "partial", "time", "copy", "string", "io"}
_FROM_IMPORT = re.compile(r"\bfrom\s+([\w.]+)\s+import\s+([^\n;]+)")


def import_from_context(name: str, tutorial_md: str) -> str | None:
    """The import the tutorial itself shows for `name`, anywhere (code, prose, any block)."""
    for m in _FROM_IMPORT.finditer(tutorial_md):  # anywhere in a line: prose often quotes it
        tokens = re.findall(r"[A-Za-z_]\w*", m[2])
        if name in tokens and tokens[tokens.index(name) - 1 : tokens.index(name)] != ["as"]:
            return f"from {m[1]} import {name}"
    m = re.search(rf"^\s*import\s+([\w.]+)\s+as\s+{re.escape(name)}\s*$", tutorial_md, re.M)
    return f"import {m[1]} as {name}" if m else None


def _bound_and_used(source: str) -> tuple[set[str], set[str]] | None:
    try:
        tree = ast.parse(strip_magics(source))
    except SyntaxError:
        return None
    bound: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Name):
            (used if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound, used


def missing_imports(blocks, tutorial_md: str = "") -> dict[str, list[str]]:
    """block id -> import lines to add (in order), by static analysis of the python blocks."""
    defined: set[str] = set()
    out: dict[str, list[str]] = {}
    for b in blocks:
        if b.lang != "python":
            continue
        got = _bound_and_used(b.source)
        if got is None:
            continue
        bound, used = got
        for name in sorted(used & IMPORTABLE.keys()):
            if name not in defined and name not in bound:
                line = import_from_context(name, tutorial_md) or IMPORTABLE[name]
                out.setdefault(b.id, []).append(line)
                defined.add(name)
        defined |= bound
    return out


def make_prerepair(flags: PipelineFlags | None = None) -> Callable[[GraphState], dict]:
    def prerepair(state: GraphState) -> dict:
        f = flags or PipelineFlags()
        if not f.repair_defects:
            return {}
        todo = missing_imports(state.blocks, state.tutorial_md)
        if not todo:
            return {}
        blocks, entries = list(state.blocks), list(state.change_log)
        for i, b in enumerate(blocks):
            lines = todo.get(b.id)
            if not lines:
                continue
            blocks[i] = b.model_copy(update={"source": "\n".join(lines) + "\n" + b.source})
            for line in lines:
                name = line.split()[-1]
                guess = name in AMBIGUOUS and not import_from_context(name, state.tutorial_md)
                note = " (assumed: the tutorial does not say where it comes from)" if guess else ""
                entries.append(
                    ChangeLogEntry(
                        block_id=b.id,
                        kind="defect_fix",
                        description=f"Added `{line}`: the block uses `{name}` but nothing "
                        f"imports it{note}.",
                        error=f"static check: `{name}` is used but never imported or defined",
                    )
                )
        return {"blocks": blocks, "change_log": entries}

    return prerepair


prerepair = make_prerepair()

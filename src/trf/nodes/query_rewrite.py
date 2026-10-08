"""Turn sandbox errors into retrieval targets for the feedback loop.

For every failing block, read its traceback (or last error line) and name the library symbol that
broke, in the same naming scheme `extract` uses (`pandas.DataFrame.append`,
`pandas.read_csv(squeeze)`, `pydantic.BaseModel.dict`). Symbols that are new to the run are added
to `state.symbols`. `state.fresh_symbols` lists the symbols that still need evidence or a verdict
(new ones, plus known ones the diff stage had cleared as unchanged but that demonstrably break), so
`retrieve` and `diff` only do work for them. `state.error_symbols` maps each failing
block to its symbols, which `rewrite` uses to pick the verdicts to apply.

This is deterministic pattern matching, not an LLM call: errors from API rot are regular, and a
loop pass should not cost minutes of model time. A block whose error names no library symbol gets
no entry and a warning, and is left failing (it ends up `unresolved`, never "passing").
"""

from __future__ import annotations

import re
from collections.abc import Callable

from trf.state import GraphState, Symbol, SymbolKind

_ATTR_OBJ = re.compile(
    r"(?:'(?P<obj>[\w.]+)' object|type object '(?P<cls>[\w.]+)') has no attribute '(?P<attr>\w+)'"
)
_ATTR_MOD = re.compile(r"module '(?P<mod>[\w.]+)' has no attribute '(?P<attr>\w+)'")
_KWARG = re.compile(r"(?P<callee>[\w.]+)\(\) got an unexpected keyword argument '(?P<arg>\w+)'")
_IMPORT = re.compile(r"cannot import name '(?P<name>\w+)' from '(?P<mod>[\w.]+)'")
_NO_MODULE = re.compile(r"No module named '(?P<mod>[\w.]+)'")
_BACKTICK = re.compile(r"`(?P<name>[\w.]+)`")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_]\w*", text))


def _tail(symbol: str) -> str:
    """`pandas.DataFrame.to_csv(line_terminator)` -> `to_csv`."""
    return re.sub(r"\(.*\)$", "", symbol).split(".")[-1]


class _Resolver:
    def __init__(self, libraries: list[str], known: list[Symbol], source: str):
        self.libs, self.known, self.source = libraries, known, source
        self.names = {s.name for s in known}

    def lib_for(self, hint: str | None = None) -> str | None:
        """Which registered library does a bare name belong to?"""
        if not self.libs:
            return None
        if hint:
            root = hint.split(".")[0]
            if root in self.libs:
                return root
        if len(self.libs) == 1:
            return self.libs[0]
        used = [lib for lib in self.libs if re.search(rf"\b{re.escape(lib)}\b", self.source)]
        return used[0] if len(used) == 1 else None

    def known_match(self, tail: str, arg: str | None = None) -> str | None:
        """An already-known symbol with the same last component (and keyword, if given)."""
        for n in sorted(self.names):
            if _tail(n) != tail:
                continue
            if arg is None or n.endswith(f"({arg})"):
                return n
        return None

    def attribute(self, receiver: str | None, attr: str) -> str | None:
        hit = self.known_match(attr)
        if hit:
            return hit
        lib = self.lib_for(receiver)
        if lib is None:
            return None
        recv = (receiver or "").split(".")[-1]
        if lib == "pydantic" and recv != "BaseSettings":
            recv = "BaseModel"  # user-defined models: their attributes come from BaseModel
        return f"{lib}.{recv}.{attr}" if recv else f"{lib}.{attr}"


def symbols_from_error(
    error: str, source: str, libraries: list[str], known: list[Symbol]
) -> list[tuple[str, SymbolKind]]:
    """Library symbols implicated by one error, as (name, kind), most specific first."""
    r = _Resolver(libraries, known, source)
    found: list[tuple[str, SymbolKind]] = []

    def add(name: str | None, kind: SymbolKind) -> None:
        if name and name not in {n for n, _ in found}:
            found.append((name, kind))

    for m in _ATTR_MOD.finditer(error):
        lib = m["mod"].split(".")[0]
        if lib in libraries:
            name = f"{m['mod']}.{m['attr']}"
            # `pandas.np` is gone: the tutorial symbol is what it used from it (`pandas.np.mean`)
            deeper = sorted(n for n in r.names if n.startswith(name + "."))
            for n in [name, *deeper]:
                add(n, "attribute")
    for m in _ATTR_OBJ.finditer(error):
        add(r.attribute(m["obj"] or m["cls"], m["attr"]), "attribute")
    for m in _KWARG.finditer(error):
        callee, arg = m["callee"], m["arg"]
        hit = r.known_match(callee.split(".")[-1], arg)
        if hit:
            add(hit, "argument")
            continue
        lib = r.lib_for(callee)
        if lib:
            name = callee if callee.split(".")[0] == lib else f"{lib}.{callee}"
            add(f"{name}({arg})", "argument")
    for m in _IMPORT.finditer(error):
        lib = m["mod"].split(".")[0]
        if lib in libraries:
            add(f"{m['mod']}.{m['name']}", "class" if m["name"][:1].isupper() else "function")
    for m in _NO_MODULE.finditer(error):
        lib = m["mod"].split(".")[0]
        if lib in libraries and "." in m["mod"]:
            add(m["mod"], "module")
    # pydantic-style messages: "`regex` is removed", "`BaseSettings` has been moved ..."
    for m in _BACKTICK.finditer(error):
        word = m["name"]
        hit = r.known_match(word.split(".")[-1])
        if hit:
            add(hit, "attribute")
            continue
        call = re.search(rf"([A-Za-z_][\w.]*)\([^()]*\b{re.escape(word)}\s*=", source)
        lib = r.lib_for(call[1] if call else None)
        if call and lib:
            callee = call[1].split(".")[-1]
            add(f"{lib}.{callee}({word})", "argument")
        elif lib and (word[:1].isupper() or word.startswith("__")):
            owner = "BaseModel." if word.startswith("__") else ""
            add(f"{lib}.{owner}{word}", "attribute")
    # last resort: a symbol we already know is named in the error text
    if not found:
        words = _words(error.strip().splitlines()[-1] if error.strip() else "")  # message only
        for n in sorted(r.names):
            if _tail(n) in words:
                add(n, "attribute")
    return found


def make_query_rewrite() -> Callable[[GraphState], dict]:
    def query_rewrite(state: GraphState) -> dict:
        known = list(state.symbols)
        names = {s.name for s in known}
        new: list[Symbol] = []
        judged = {v.symbol for v in state.verdicts}
        fresh: list[str] = []
        error_symbols: dict[str, list[str]] = {}
        warnings = list(state.warnings)
        traces = {r.block_id: r.traceback for r in state.run_results if r.traceback}
        unexplained: list[tuple[str, str]] = []

        for b in state.blocks:
            if b.status != "failing" or b.lang != "python":
                continue
            error = "\n".join(filter(None, [traces.get(b.id), b.last_error]))
            hits = symbols_from_error(error, b.source, state.libraries, [*known, *new])
            if not hits:
                unexplained.append((b.id, b.last_error or "unknown error"))
                continue
            error_symbols[b.id] = [n for n, _ in hits]
            for name, kind in hits:
                if name not in judged and name not in fresh:
                    fresh.append(name)
                if name not in names and name not in {s.name for s in new}:
                    lib = name.split(".")[0].split("(")[0]
                    new.append(
                        Symbol(
                            name=name,
                            kind=kind,
                            library=lib,
                            old_version=state.old_versions.get(lib),
                            first_seen_in_block=b.id,
                        )
                    )
        for block_id, err in unexplained:
            # a cascade from an earlier failing block needs no warning if that block is handled
            if "downstream effect" in err and error_symbols:
                continue
            warnings.append(
                f"query_rewrite: no library symbol identified for {block_id} ({err[:120]})"
            )
        return {
            "symbols": [*state.symbols, *new],
            "error_symbols": error_symbols,
            "fresh_symbols": fresh,
            "warnings": warnings,
        }

    return query_rewrite


query_rewrite = make_query_rewrite()

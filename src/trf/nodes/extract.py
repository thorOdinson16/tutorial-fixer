"""Extract libraries, imported/used symbols, keyword arguments, Config keys and install commands.

Symbol naming: fully-qualified dotted names, e.g. `pandas.read_csv`, `pydantic.validator`.

Tutorial blocks share ONE namespace, so analysis is sequential over all python blocks with
persistent state: `import pandas as pd` in block 2 makes `pd.` resolvable in block 5, and
`df = pd.read_csv(..)` in block 2 makes `df.append(..)` in block 3 `pandas.DataFrame.append`.

Method/attribute calls on instances are resolved with a light, flow-insensitive type tracker, not
real type inference. `class M(BaseModel)` -> `M.parse_obj(..)` is `pydantic.BaseModel.parse_obj`;
a column subscript of a DataFrame is a Series; `.str` / `.dt` accessors, `groupby` objects,
`df.index` and `Model.__fields__[..]` have their own types (see TYPE_HINTS). Keyword args of
library calls become `<callee>(<kwarg>)` symbols (kind `argument`). Attribute access on instances
of user-defined models (`user.name`) is a field, not a library symbol, and is ignored.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field

from trf.config import LIBRARIES
from trf.nodes.ingest import _INSTALL, _norm
from trf.state import CodeBlock, GraphState, Symbol, SymbolKind

# Library knowledge for the heuristic tracker. Extend per library; unknown stays untyped.
RETURN_TYPE_HINTS = {  # prefix of a library call -> type of its result
    "pandas.read_": "pandas.DataFrame",
    "pandas.concat": "pandas.DataFrame",
    "pandas.merge": "pandas.DataFrame",
}
ATTR_HINTS = {  # "<type>.<attr>" -> type of that attribute
    "pandas.DataFrame.index": "pandas.Index",
    "pandas.Series.index": "pandas.Index",
    "pandas.DataFrame.columns": "pandas.Index",
    "pandas.Series.str": "pandas.Series.str",
    "pandas.Series.dt": "pandas.Series.dt",
    "pydantic.BaseModel.__fields__": "pydantic.fields.FieldMap",
    "pydantic.BaseModel.model_fields": "pydantic.fields.FieldMap",
}
PASSTHROUGH_ATTRS = {"loc", "iloc", "at", "iat"}  # `df.loc[..]` keeps the receiver's type
METHOD_HINTS = {  # "<type>.<method>" -> type of the call's result
    "pandas.DataFrame.groupby": "pandas.DataFrameGroupBy",
    "pandas.Series.groupby": "pandas.SeriesGroupBy",
}
RECV_RESULT = {  # any method called on this type returns that type
    "pandas.Series.str": "pandas.Series",
    "pandas.Series.dt": "pandas.Series",
    "pandas.DataFrameGroupBy": "pandas.DataFrame",
    "pandas.SeriesGroupBy": "pandas.Series",
}
SUBSCRIPT_HINTS = {  # `x["name"]` with a string key
    "pandas.DataFrame": "pandas.Series",
    "pandas.DataFrameGroupBy": "pandas.SeriesGroupBy",
    "pydantic.fields.FieldMap": "pydantic.fields.ModelField",
}
IMPORT_TO_PYPI = {
    "sklearn": "scikit-learn", "PIL": "pillow", "yaml": "pyyaml", "cv2": "opencv-python-headless",
    "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "dotenv": "python-dotenv",
}  # fmt: skip
_MAGIC = re.compile(r"^\s*[!%]")


def strip_magics(src: str) -> str:
    """Replace Jupyter `!cmd` / `%magic` lines with `pass` so the rest still parses."""
    return "\n".join("pass" if _MAGIC.match(ln) else ln for ln in src.split("\n"))


def _root_module(dotted: str) -> str:
    return dotted.split(".", 1)[0]


@dataclass
class Analysis:
    symbols: dict[str, Symbol] = field(default_factory=dict)  # first sighting of each symbol
    imports: set[str] = field(default_factory=set)  # every top-level imported module
    used: dict[str, set[str]] = field(default_factory=dict)  # block id -> symbols it uses


class _Analyzer:
    def __init__(self, libs: set[str]):
        self.libs = libs
        self.block_id = ""
        self.alias: dict[str, str] = {}  # local name -> dotted lib path
        self.classes: dict[str, str] = {}  # local class -> library base type
        self.types: dict[str, str] = {}  # local variable -> type string
        self.out = Analysis()
        self.call_funcs: set[int] = set()
        self.local_types: set[str] = set()

    # -- helpers ------------------------------------------------------------
    def emit(self, name: str, kind: SymbolKind) -> None:
        if name not in self.out.symbols:
            self.out.symbols[name] = Symbol(
                name=name, kind=kind, library=_root_module(name), first_seen_in_block=self.block_id
            )
        self.out.used.setdefault(self.block_id, set()).add(name)

    @staticmethod
    def _flatten(node: ast.AST) -> tuple[ast.AST, list[str]]:
        attrs: list[str] = []
        while isinstance(node, ast.Attribute):
            attrs.append(node.attr)
            node = node.value
        return node, attrs[::-1]

    def _lib_dotted(self, node: ast.AST) -> str | None:
        root, attrs = self._flatten(node)
        if isinstance(root, ast.Name) and root.id in self.alias:
            return ".".join([self.alias[root.id], *attrs])
        return None

    def ty(self, node: ast.AST) -> str | None:
        """Best-effort type of an expression, or None when unknown."""
        if isinstance(node, ast.Name):
            return self.types.get(node.id) or self.classes.get(node.id)
        if isinstance(node, ast.Call):
            f = node.func
            q = self._lib_dotted(f)
            if q:
                for prefix, t in RETURN_TYPE_HINTS.items():
                    if q.startswith(prefix):
                        return t
                return q if q.rsplit(".", 1)[-1][:1].isupper() else f"{_root_module(q)}.*"
            if isinstance(f, ast.Attribute):
                recv = self.ty(f.value)
                if recv is None:
                    return None
                return METHOD_HINTS.get(f"{recv}.{f.attr}") or RECV_RESULT.get(recv, recv)
            return self.ty(f)  # call of a local class -> instance of its library base
        if isinstance(node, ast.Attribute):
            recv = self.ty(node.value)
            if recv is None:
                return None
            hint = ATTR_HINTS.get(f"{recv}.{node.attr}")
            return hint or (recv if node.attr in PASSTHROUGH_ATTRS else None)
        if isinstance(node, ast.Subscript):
            recv = self.ty(node.value)
            key = node.slice
            if (
                recv in SUBSCRIPT_HINTS
                and isinstance(key, ast.Constant)
                and isinstance(key.value, str)
            ):
                return SUBSCRIPT_HINTS[recv]
            return recv
        return None

    def _chain(self, n: ast.Attribute) -> tuple[list[str], int] | None:
        """Qualified symbol for each attribute step of `n` (stops where the type is lost).

        Returns (symbols, number_of_steps_in_the_expression) or None if the receiver is untyped.
        """
        root, attrs = self._flatten(n)
        q = self._lib_dotted(n)
        if q:
            return [q], 1
        cur = (
            self.types.get(root.id) or self.classes.get(root.id)
            if isinstance(root, ast.Name)
            else self.ty(root)
        )
        if not cur:
            return None
        out: list[str] = []
        for attr in attrs:
            sym = f"{cur}.{attr}"
            out.append(sym)
            nxt = ATTR_HINTS.get(sym) or (cur if attr in PASSTHROUGH_ATTRS else None)
            if nxt is None:
                break
            cur = nxt
        return out, len(attrs)

    # -- passes -------------------------------------------------------------
    def collect_imports(self, tree: ast.AST) -> None:
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.out.imports.add(_root_module(a.name))
                    if _root_module(a.name) in self.libs:
                        self.alias[a.asname or _root_module(a.name)] = (
                            a.name if a.asname else _root_module(a.name)
                        )
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                self.out.imports.add(_root_module(n.module))
                if _root_module(n.module) in self.libs:
                    for a in n.names:
                        full = f"{n.module}.{a.name}"
                        self.alias[a.asname or a.name] = full
                        self.emit(full, "class" if a.name[:1].isupper() else "function")

    def collect_types(self, tree: ast.AST) -> None:
        for _ in range(3):  # settle chained definitions without caring about order
            for n in ast.walk(tree):
                if isinstance(n, ast.ClassDef):
                    for b in n.bases:
                        t = self._lib_dotted(b) or self.classes.get(getattr(b, "id", ""))
                        if t:
                            self.classes[n.name] = t
                elif isinstance(n, ast.Assign | ast.AnnAssign):
                    value = n.value
                    targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                    t = self.ty(value) if value is not None else None
                    for tgt in targets:
                        if isinstance(tgt, ast.Name) and t:
                            self.types[tgt.id] = t

    def collect_usage(self, tree: ast.AST) -> None:
        self.call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.local_types = set(self.classes.values())
        inner: set[int] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef) and n.name in self.classes:
                self._config_keys(n)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in self.alias:
                full = self.alias[n.id]
                if "." in full and full != _root_module(full):
                    self.emit(
                        full, "class" if full.rsplit(".", 1)[-1][:1].isupper() else "function"
                    )
            if isinstance(n, ast.Attribute):
                if isinstance(n.value, ast.Attribute):
                    inner.add(id(n.value))
                if id(n) in inner:
                    continue
                self._attribute(n)
            if isinstance(n, ast.Call):
                self._call_kwargs(n)

    def _config_keys(self, cls: ast.ClassDef) -> None:
        base = self.classes[cls.name]
        for inner in cls.body:
            if isinstance(inner, ast.ClassDef) and inner.name == "Config":
                self.emit(f"{base}.Config", "attribute")
                for stmt in inner.body:
                    if isinstance(stmt, ast.Assign | ast.AnnAssign):
                        tg = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                        for t in tg:
                            if isinstance(t, ast.Name):
                                self.emit(f"{base}.Config.{t.id}", "config_key")

    def _attribute(self, n: ast.Attribute) -> None:
        chain = self._chain(n)
        if chain is None:
            return
        syms, steps = chain
        is_call = id(n) in self.call_funcs
        root, attrs = self._flatten(n)
        if self._lib_dotted(n):  # module-rooted: pd.read_csv, pydantic.Field
            q = syms[0]
            if q != _root_module(q):
                upper = attrs[-1][:1].isupper()
                self.emit(q, "class" if upper else "function" if is_call else "attribute")
            return
        for i, sym in enumerate(syms):
            direct_call = is_call and i == steps - 1
            receiver_type = sym.rsplit(".", 1)[0]
            attr = sym.rsplit(".", 1)[1]
            # Field access on user-defined models (`user.name`) is not a library symbol.
            if receiver_type in self.local_types and not direct_call and not attr.startswith("_"):
                continue
            self.emit(sym, "method" if direct_call else "attribute")

    def _callee(self, f: ast.AST) -> str | None:
        q = self._lib_dotted(f)  # `Field(...)` after `from pydantic import Field`, or `pd.read_csv`
        if q:
            return q
        if not isinstance(f, ast.Attribute):
            return None
        chain = self._chain(f)
        if chain is None:
            return None
        syms, steps = chain
        return syms[-1] if len(syms) == steps else None

    def _call_kwargs(self, n: ast.Call) -> None:
        callee = self._callee(n.func)
        if callee:
            for kw in n.keywords:
                if kw.arg:
                    self.emit(f"{callee}({kw.arg})", "argument")

    def feed(self, block_id: str, source: str) -> None:
        self.block_id = block_id
        try:
            tree = ast.parse(strip_magics(source))
        except SyntaxError:
            for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", source, re.MULTILINE):
                self.out.imports.add(m[1])
            return
        self.collect_imports(tree)
        self.collect_types(tree)
        self.collect_usage(tree)


def analyze_blocks(blocks: list[CodeBlock], libs: set[str] | None = None) -> Analysis:
    """Analyse all python blocks in order with shared alias/type state (one tutorial namespace)."""
    a = _Analyzer(set(LIBRARIES) if libs is None else libs)
    for b in blocks:
        if b.lang == "python":
            a.feed(b.id, b.source)
    return a.out


def analyze_block(block_id: str, source: str, libs: set[str]) -> tuple[list[Symbol], set[str]]:
    """Single-block convenience wrapper (no cross-block state)."""
    a = _Analyzer(libs)
    a.feed(block_id, source)
    return list(a.out.symbols.values()), a.out.imports


def extract(state: GraphState) -> dict:
    libs = set(LIBRARIES)
    analysis = analyze_blocks(state.blocks, libs)
    symbols: dict[str, Symbol] = dict(analysis.symbols)
    used_libs: set[str] = set()

    for b in state.blocks:
        if b.lang in {"bash", "sh", "shell", "console", "zsh", "text", ""}:
            for line in b.source.splitlines():
                line = line.strip().lstrip("$! ")
                m = _INSTALL.search(line)
                if not m:
                    continue
                mentioned = [
                    lib
                    for lib in libs
                    if re.search(rf"\b{re.escape(_norm(lib))}\b", _norm(m["rest"]))
                ]
                for lib in mentioned:
                    used_libs.add(lib)
                    symbols.setdefault(
                        line, Symbol(name=line, kind="cli", library=lib, first_seen_in_block=b.id)
                    )

    used_libs |= {lib for lib in libs if lib in analysis.imports}
    return {
        "symbols": list(symbols.values()),
        "libraries": sorted(used_libs),
        "extra_packages": packages_for_imports(analysis.imports),
    }


# Names a tutorial's own project modules usually have. PyPI holds unrelated packages under several
# of these (`app`, `utils`, ...), so installing them would run foreign code and still not help.
LOCAL_MODULE_NAMES = {
    "app", "main", "utils", "config", "settings", "models", "schemas", "crud", "db", "database",
    "core", "api", "tests", "src", "helpers", "common", "lib", "deps", "routers", "services",
}  # fmt: skip


def packages_for_imports(imported: set[str]) -> list[str]:
    """Third-party pip names for imported modules.

    Excluded: stdlib, the registered libraries themselves, and modules those libraries install
    as dependencies (`LibraryConfig.bundled_imports`, e.g. numpy for pandas).
    """
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    bundled = {m for lib, cfg in LIBRARIES.items() if lib in imported for m in cfg.bundled_imports}
    return sorted(
        {
            _norm(IMPORT_TO_PYPI.get(m, m))
            for m in imported
            if m not in stdlib
            and m not in LIBRARIES
            and m not in bundled
            and m not in LOCAL_MODULE_NAMES
        }
    )


def extra_packages_for(blocks: list[CodeBlock]) -> list[str]:
    return packages_for_imports(analyze_blocks(blocks).imports)

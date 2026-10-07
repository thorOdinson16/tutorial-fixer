"""Extract libraries, imported/used symbols, keyword arguments, Config keys and install commands.

Symbol naming: fully-qualified dotted names, e.g. `pandas.read_csv`, `pydantic.validator`.
Method calls on instances are resolved with a light, flow-insensitive type tracker:
`class M(BaseModel)` -> `M.parse_obj(...)` is `pydantic.BaseModel.parse_obj`;
`df = pd.read_csv(..)` -> `df.append(..)` is `pandas.DataFrame.append`. When the receiver type
is unknown but library-derived the symbol is `<lib>.*.<attr>`. Keyword args of library calls
become `<callee>(<kwarg>)` symbols (kind `argument`). This is a heuristic, not type inference.
"""

from __future__ import annotations

import ast
import re
import sys

from trf.config import LIBRARIES
from trf.nodes.ingest import _INSTALL, _norm
from trf.state import CodeBlock, GraphState, Symbol, SymbolKind

# Prefix of a library call -> type of its result (so `.append` etc. can be attributed).
RETURN_TYPE_HINTS = {
    "pandas.read_": "pandas.DataFrame",
    "pandas.concat": "pandas.DataFrame",
    "pandas.merge": "pandas.DataFrame",
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


class _Analyzer:
    def __init__(self, block_id: str, libs: set[str]):
        self.block_id = block_id
        self.libs = libs
        self.alias: dict[str, str] = {}  # local name -> dotted lib path
        self.classes: dict[str, str] = {}  # local class -> library base type
        self.types: dict[str, str] = {}  # local variable -> type string
        self.symbols: dict[str, Symbol] = {}
        self.imports: set[str] = set()  # all top-level imported modules

    # -- helpers ------------------------------------------------------------
    def emit(self, name: str, kind: SymbolKind) -> None:
        if name not in self.symbols:
            self.symbols[name] = Symbol(
                name=name, kind=kind, library=_root_module(name), first_seen_in_block=self.block_id
            )

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
        if isinstance(node, ast.Name):
            return self.types.get(node.id) or self.classes.get(node.id)
        if isinstance(node, ast.Call):
            q = self._lib_dotted(node.func)
            if q:
                for prefix, t in RETURN_TYPE_HINTS.items():
                    if q.startswith(prefix):
                        return t
                return q if q.rsplit(".", 1)[-1][:1].isupper() else f"{_root_module(q)}.*"
            return self.ty(node.func)
        if isinstance(node, ast.Attribute | ast.Subscript):
            return self.ty(node.value)
        return None

    # -- passes -------------------------------------------------------------
    def collect_imports(self, tree: ast.AST) -> None:
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.imports.add(_root_module(a.name))
                    if _root_module(a.name) in self.libs:
                        self.alias[a.asname or _root_module(a.name)] = (
                            a.name if a.asname else _root_module(a.name)
                        )
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                self.imports.add(_root_module(n.module))
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
        root, attrs = self._flatten(n)
        is_call = id(n) in self.call_funcs
        q = self._lib_dotted(n)
        if q:
            if q != _root_module(q):
                upper = attrs[-1][:1].isupper()
                self.emit(q, "class" if upper else "function" if is_call else "attribute")
            return
        if isinstance(root, ast.Name):
            t = self.types.get(root.id) or self.classes.get(root.id)
        else:
            t = self.ty(root)
        if not t or not attrs:
            return
        direct_call = is_call and len(attrs) == 1
        # Field access on user-defined models (`user.name`) is not a library symbol.
        if t in self.local_types and not direct_call and not attrs[0].startswith("_"):
            return
        self.emit(f"{t}.{attrs[0]}", "method" if direct_call else "attribute")

    def _callee(self, f: ast.AST) -> str | None:
        q = self._lib_dotted(f)
        if q:
            return q
        if isinstance(f, ast.Attribute) and not isinstance(f.value, ast.Attribute):
            t = self.ty(f.value)
            return f"{t}.{f.attr}" if t else None
        return None

    def _call_kwargs(self, n: ast.Call) -> None:
        callee = self._callee(n.func)
        if callee:
            for kw in n.keywords:
                if kw.arg:
                    self.emit(f"{callee}({kw.arg})", "argument")


def analyze_block(block_id: str, source: str, libs: set[str]) -> tuple[list[Symbol], set[str]]:
    a = _Analyzer(block_id, libs)
    try:
        tree = ast.parse(strip_magics(source))
    except SyntaxError:
        for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", source, re.MULTILINE):
            a.imports.add(m[1])
        return [], a.imports
    a.collect_imports(tree)
    a.collect_types(tree)
    a.collect_usage(tree)
    return list(a.symbols.values()), a.imports


def extract(state: GraphState) -> dict:
    libs = set(LIBRARIES)
    symbols: dict[str, Symbol] = {}
    imported: set[str] = set()
    used_libs: set[str] = set()

    for b in state.blocks:
        if b.lang == "python":
            syms, imps = analyze_block(b.id, b.source, libs)
            imported |= imps
            for s in syms:
                symbols.setdefault(s.name, s)
        elif b.lang in {"bash", "sh", "shell", "console", "zsh", "text", ""}:
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

    used_libs |= {lib for lib in libs if lib in imported}
    return {
        "symbols": list(symbols.values()),
        "libraries": sorted(used_libs),
        "extra_packages": packages_for_imports(imported),
    }


def packages_for_imports(imported: set[str]) -> list[str]:
    """Third-party pip names for imported modules (stdlib and registered libs excluded)."""
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    return sorted(
        {
            _norm(IMPORT_TO_PYPI.get(m, m))
            for m in imported
            if m not in stdlib and m not in LIBRARIES
        }
    )


def extra_packages_for(blocks: list[CodeBlock]) -> list[str]:
    imported: set[str] = set()
    for b in blocks:
        if b.lang == "python":
            imported |= analyze_block(b.id, b.source, set())[1]
    return packages_for_imports(imported)

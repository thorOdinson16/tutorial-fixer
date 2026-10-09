"""Mine deprecation facts from a library's own SOURCE CODE (for changes release notes omit).

Release notes do not mention everything: `np.reshape(newshape=...)` became `shape=` and
Matplotlib's `linthreshy` became `linthresh`, yet neither is findable in the notes. The code of the
last release before the removal says it plainly, in three places this module reads:

  * decorators: `@_api.rename_parameter("3.3", "linthreshy", "linthresh")`,
    `@deprecate_kwarg(old_arg_name="inplace", ...)`, `@_api.deprecated("3.7", alternative=...)`,
    `@typing_extensions.deprecated("The `dict` method is deprecated; use `model_dump` instead.")`;
  * warning calls whose message talks about deprecation or removal
    (`warnings.warn("`newshape` ... is deprecated, use `shape=...`", DeprecationWarning)`);
  * `.. deprecated::` / `.. versionchanged::` directives in docstrings.

Each hit becomes one `Chunk` that names the enclosing function or class, so the symbol index and
BM25 can find it by the name or the keyword. The source URL points at the file at its tag.
"""

from __future__ import annotations

import ast
import re

from trf.state import Chunk

MAX_CHUNKS_PER_FILE = 300
_DECORATORS = re.compile(
    r"deprecat|rename_parameter|rename_param|deprecate_kwarg|deprecate_nonkeyword|"
    r"warn_deprecated|make_keyword_only",
    re.I,
)
_WARN_FUNCS = re.compile(r"(^|\.)(warn|warn_deprecated|_warn_deprecated|warn_external)$")
_MESSAGE = re.compile(
    r"deprecat|removed|renamed|no longer|will be (removed|an error)|instead|replaced", re.I
)
_DIRECTIVE = re.compile(
    r"\.\. (?P<kind>deprecated|versionchanged|versionremoved)::\s*(?P<ver>[\w.]+)\s*\n"
    r"(?P<body>(?:[ \t]+[^\n]*\n?)*)"
)
_VERSION = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _text(node: ast.AST) -> str:
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:  # noqa: BLE001  (unparse can fail on odd nodes; skip them)
        return ""


def _strings(node: ast.AST) -> list[str]:
    """String literals (and f-strings, unparsed) anywhere inside `node`, longest first."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(" ".join(n.value.split()))
        elif isinstance(n, ast.JoinedStr):
            out.append(" ".join(_text(n).strip("f'\"").split()))
    return sorted(set(out), key=len, reverse=True)


def _first_version(text: str) -> str | None:
    m = _VERSION.search(text)
    if not m:
        return None
    v = m.group(1)
    return v if v.count(".") == 2 else f"{v}.0"


def mine(source: str, library: str, url: str, default_version: str | None = None) -> list[Chunk]:
    """Chunks describing deprecations found in one Python source file."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    facts: list[tuple[str, str, str | None]] = []  # (qualname, text, version)

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                qual = ".".join([*scope, child.name])
                for dec in child.decorator_list:
                    name = _dotted(dec)
                    if _DECORATORS.search(name):
                        txt = _text(dec)
                        facts.append((qual, f"decorator {txt}", _first_version(txt)))
                doc = ast.get_docstring(child) or ""
                for m in _DIRECTIVE.finditer(doc + "\n"):
                    body = " ".join(m["body"].split())[:260]
                    facts.append((qual, f"{m['kind']} {m['ver']}: {body}", m["ver"]))
                visit(child, [*scope, child.name])
            elif (
                isinstance(child, ast.Assign)
                and isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Call)
                and _DECORATORS.search(_dotted(child.value.func))
                and isinstance(child.targets[0], ast.Name)
            ):  # `get_cmap = _api.deprecated("3.7", ...)(_get_cmap)`: a deprecated alias
                txt = _text(child.value.func)
                qual = ".".join([*scope, child.targets[0].id])
                facts.append((qual, f"deprecated alias {txt}", _first_version(txt)))
                visit(child, scope)
            elif isinstance(child, ast.Call) and _WARN_FUNCS.search(_dotted(child.func)):
                msgs = [s for s in _strings(child) if _MESSAGE.search(s) and len(s) > 20]
                if msgs:
                    qual = ".".join(scope) or "<module>"
                    facts.append((qual, f"warns: {msgs[0][:320]}", _first_version(msgs[0])))
                visit(child, scope)
            else:
                visit(child, scope)

    visit(tree, [])
    # parameter names built per axis (`f"linthresh{axis_name}"`): spell out both axes
    expanded = []
    for qual, text, version in facts:
        if "{axis_name}" in text or "{axis}" in text:
            for ax in ("x", "y"):
                expanded.append(
                    (qual, text.replace("{axis_name}", ax).replace("{axis}", ax), version)
                )
        expanded.append((qual, text, version))
    facts = expanded
    tail = "/".join(url.removesuffix(".py").split("/")[-2:])  # unique across `_base.py` files
    slug = re.sub(r"[^A-Za-z0-9]+", "-", tail).strip("-")
    out: list[Chunk] = []
    seen: set[str] = set()
    for qual, text, version in facts:
        body = f"{library}.{qual}: {text}"
        if body in seen:
            continue
        seen.add(body)
        out.append(
            Chunk(
                id=f"{library}:src-{slug}:{len(out):04d}",
                text=body,
                library=library,
                version_introduced=version or default_version,
                symbol=qual.split(".")[-1],
                doc_type="docs",
                source_url=url,
            )
        )
        if len(out) >= MAX_CHUNKS_PER_FILE:
            break
    return out

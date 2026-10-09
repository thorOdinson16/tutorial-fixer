"""Parse markdown into prose + code segments, detect language, infer date and version pins.

Segments are lossless: `render_segments(split_segments(md)) == md` for any input.
Known limitation: fences indented more than 3 spaces (e.g. inside list items) stay prose.
"""

from __future__ import annotations

import ast
import re
import shlex
from datetime import date

from trf.session import new_block
from trf.state import CodeBlock, GraphState, Segment

_FENCE_OPEN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
PY_LANGS = {"python", "py", "python3", "py3"}
_NOT_SESSIONS = {"bash", "sh", "shell", "zsh", "console", "text", "json", "yaml", "toml", "sql"}
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def split_segments(md: str) -> list[Segment]:
    lines = md.splitlines(keepends=True)
    segs: list[Segment] = []
    prose: list[str] = []
    n = 0
    i = 0

    def flush() -> None:
        if prose:
            segs.append(Segment(kind="prose", text="".join(prose)))
            prose.clear()

    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i].rstrip("\r\n"))
        if m and not (m["fence"][0] == "`" and "`" in m["info"]):
            ch, size = m["fence"][0], len(m["fence"])
            close = re.compile(rf"^ {{0,3}}{re.escape(ch)}{{{size},}}\s*$")
            j = i + 1
            while j < len(lines) and not close.match(lines[j].rstrip("\r\n")):
                j += 1
            if j < len(lines):
                flush()
                n += 1
                segs.append(
                    Segment(
                        kind="code",
                        text="".join(lines[i + 1 : j]),
                        block_id=f"b{n}",
                        fence_open=lines[i],
                        fence_close=lines[j],
                    )
                )
                i = j + 1
                continue
        prose.append(lines[i])
        i += 1
    flush()
    return segs


def render_segments(segs: list[Segment]) -> str:
    return "".join(
        s.text if s.kind == "prose" else s.fence_open + s.text + s.fence_close for s in segs
    )


def _info_lang(info: str) -> str:
    tok = info.strip().split(maxsplit=1)[0] if info.strip() else ""
    return tok.strip("{}.").lower()


def detect_lang(info: str, body: str) -> str:
    """Normalised language: 'python' for runnable python, 'pycon' for REPL transcripts, else raw."""
    lang = _info_lang(info)
    if lang in PY_LANGS:
        return "python"
    if lang in {"pycon", "python-repl", "ipython", "ipython3"}:
        return "pycon"
    if lang:
        return lang
    if body.lstrip().startswith(">>>"):
        return "pycon"
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return "text"
    interesting = any(
        isinstance(n, ast.Import | ast.ImportFrom | ast.FunctionDef | ast.ClassDef | ast.Assign)
        for n in tree.body
    )
    return "python" if interesting else "text"


def infer_date(prose: str) -> date | None:
    """First publication-looking date near the top of the prose (ISO or 'Month D, YYYY')."""
    head = "\n".join(prose.splitlines()[:40])
    m = re.search(r"\b(20[0-3]\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", head)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b([A-Z][a-z]{2})[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?,? (20[0-3]\d)\b", head)
    if m and m[1].lower() in _MONTHS:
        try:
            return date(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
        except ValueError:
            return None
    return None


_INSTALL = re.compile(r"(?:pip3?|pip|uv pip|uv|poetry|conda)\s+(?:install|add)\s+(?P<rest>.*)")
_REQ = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9_.-]*)(?:\[[^\]]*\])?(?P<spec>(?:(?:==|>=|<=|~=|!=|<|>)[0-9][^,\s]*,?)+)?$"
)
_REQ_LINE = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z0-9_.-]*)\s*(?P<spec>==\s*[0-9][^\s#]*)\s*$")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def extract_pins(md: str) -> dict[str, str]:
    """Pins from `pip install x==1` style commands and requirements.txt-style lines."""
    pins: dict[str, str] = {}
    for line in md.splitlines():
        line = line.strip().lstrip("!$ ")
        m = _INSTALL.search(line)
        if m:
            try:
                toks = shlex.split(m["rest"])
            except ValueError:
                toks = m["rest"].split()
            for tok in toks:
                if tok.startswith("-"):
                    continue
                r = _REQ.match(tok)
                if r and r["spec"]:
                    pins[_norm(r["name"])] = r["spec"].rstrip(",")
            continue
        m = _REQ_LINE.match(line)
        if m:
            pins[_norm(m["name"])] = re.sub(r"\s+", "", m["spec"])
    return pins


def ingest(state: GraphState) -> dict:
    segs = split_segments(state.tutorial_md)
    blocks = []
    for s in segs:
        if s.kind != "code" or not s.block_id:
            continue
        info = s.fence_open.strip().lstrip("`~")
        session = new_block(s.block_id, s.text) if _info_lang(info) not in _NOT_SESSIONS else None
        blocks.append(session or CodeBlock.new(s.block_id, s.text, detect_lang(info, s.text)))
    prose = "".join(s.text for s in segs if s.kind == "prose")
    return {
        "segments": segs,
        "blocks": blocks,
        "tutorial_date": infer_date(prose),
        "pins": extract_pins(state.tutorial_md),
    }

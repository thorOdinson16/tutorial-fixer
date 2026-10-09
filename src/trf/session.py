"""Interactive sessions (`>>> code`, `... continuation`, output lines) as tutorial code blocks.

Documentation is often written as a REPL transcript. The pipeline needs the code (to analyse, run
and edit it) and the final Markdown must still look like a session. So a session block keeps:

  * `source`: just the code, prompts and outputs removed (what runs and what the LLM edits);
  * `session`: the original statements with their printed output lines.

Rendering never changes what was not edited: an unedited block is written back byte for byte. An
edited block is re-rendered as a session: statements are found again with `ast`, each gets a `>>> `
first line and `... ` continuations, and the output lines of every statement whose code did not
change are kept. Output of a statement that WAS edited is dropped, because it may no longer be what
the code prints. If the edited code does not parse as a session it falls back to a plain code block.
"""

from __future__ import annotations

import ast
import re

from trf.state import CodeBlock, SessionStmt

_PROMPT = re.compile(r"^>>>( |$)")
_CONT = re.compile(r"^\.\.\.( |$)")
SESSION_FENCE_LANGS = {"pycon", "python-repl", "ipython", "ipython3", "doctest", "python", "py", ""}


def is_session(body: str) -> bool:
    for ln in body.splitlines():
        if ln.strip():
            return bool(_PROMPT.match(ln))
    return False


def parse_session(text: str) -> list[SessionStmt] | None:
    """The statements of a transcript, or None if the text is not a clean session."""
    stmts: list[SessionStmt] = []
    for ln in text.splitlines():
        if _PROMPT.match(ln):
            stmts.append(SessionStmt(code=[ln[4:]], output=[]))
        elif stmts and not stmts[-1].output and _CONT.match(ln):
            stmts[-1].code.append(ln[4:])
        elif stmts:
            stmts[-1].output.append(ln)
        elif ln.strip():
            return None  # text before the first prompt: not a pure session
    return stmts or None


def session_code(stmts: list[SessionStmt]) -> str:
    return "\n".join(line for s in stmts for line in s.code) + "\n"


def new_block(block_id: str, body: str, lang: str = "python") -> CodeBlock | None:
    """A python CodeBlock for a session body, or None if `body` is not a session."""
    stmts = parse_session(body) if is_session(body) else None
    if stmts is None:
        return None
    code = session_code(stmts)
    b = CodeBlock.new(block_id, code, lang)
    return b.model_copy(update={"session": stmts})


def _statement_spans(source: str) -> list[tuple[int, int]] | None:
    """1-based (first line, last line) of each top-level statement; None if it does not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    spans = []
    for node in tree.body:
        first = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])
        spans.append((first, node.end_lineno or node.lineno))
    return spans


def render_body(original_text: str, block: CodeBlock) -> tuple[str, bool]:
    """(text between the fences, whether it is still a session).

    False means the block fell back to plain code and the fence's language should say so.
    """
    if not block.session or block.source == block.original_source:
        return original_text, True
    spans = _statement_spans(block.source)
    if spans is None:
        return block.source, False
    old_output = {"\n".join(st.code).rstrip("\n"): st.output for st in block.session}
    lines = block.source.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out: list[str] = []
    covered = 0  # last source line (1-based) already rendered
    for first, last in spans:
        for no in range(covered + 1, first):  # comments between statements keep a prompt
            if lines[no - 1].strip():
                out.append(">>> " + lines[no - 1])
        for no in range(first, last + 1):
            line = lines[no - 1]
            lead = ">>>" if no == first else "..."
            out.append(f"{lead} {line}" if line else lead)
        covered = last
        if last > first and covered < len(lines) and not lines[covered].strip():
            out.append("...")  # the blank line that ends a compound statement
            covered += 1
        out.extend(old_output.get("\n".join(lines[first - 1 : last]).rstrip("\n"), []))
    for no in range(covered + 1, len(lines) + 1):  # trailing comments
        if lines[no - 1].strip():
            out.append(">>> " + lines[no - 1])
    return "\n".join(out) + "\n", True


_LANG = re.compile(r"^(?P<ticks>[`~]{3,})\s*(?P<lang>[\w+-]*)(?P<rest>.*)$")


def plain_fence(fence_open: str) -> str:
    """The opening fence with a session language swapped for `python` (bare fences stay bare)."""
    body = fence_open.rstrip("\r\n")
    eol = fence_open[len(body) :]
    m = _LANG.match(body)
    if not m or not m["lang"] or m["lang"].lower() == "python":
        return fence_open
    if m["lang"].lower() in {"pycon", "python-repl", "ipython", "ipython3", "doctest"}:
        return f"{m['ticks']}python{m['rest']}{eol}"
    return fence_open

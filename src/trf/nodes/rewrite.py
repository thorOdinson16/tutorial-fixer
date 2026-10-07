"""Patch only the affected code blocks; prose is never touched.

Pass 1 (loop_count == 0): rewrite python blocks that use a symbol with a non-unchanged verdict
(or every python block in the no-retrieval LLM-only baseline). Later passes: only blocks the
sandbox reported failing, with their last error in the prompt.

Safeguards, all failing safe to "keep the original block":
  * the new code must parse; one retry feeds the SyntaxError back;
  * a changed block must carry >= 1 change entry that maps to a cited verdict (no uncited edits).
Deterministic edits (no LLM): pinned versions in install blocks -> target, and newly required
packages appended to the install command.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from pydantic import BaseModel, Field

from trf.config import LIBRARIES, PipelineFlags
from trf.llm import LLM, LLMError
from trf.nodes.extract import analyze_blocks, extra_packages_for, strip_magics
from trf.state import ChangeLogEntry, Chunk, CodeBlock, DiffVerdict, GraphState

SYSTEM = (
    "You update Python tutorial code so it runs on newer library versions. Make the smallest "
    "edits that are needed: keep structure, names, comments, prints and output identical. "
    "Never add explanations or markdown fences."
)
MAX_EVIDENCE_CHUNKS = 6
CHUNK_CHARS = 700
SHELL_BLOCK_LANGS = {"bash", "sh", "shell", "console", "zsh", "text", ""}
_INSTALL_LINE = re.compile(r"(?:pip3?|uv pip|uv|poetry|conda)\s+(?:install|add)\b")


class ChangeOut(BaseModel):
    symbol: str | None = None
    description: str = ""


class RewriteOut(BaseModel):
    code: str
    changes: list[ChangeOut] = Field(default_factory=list)


# --- helpers ------------------------------------------------------------------------------


def strip_fences(code: str) -> str:
    m = re.match(r"^\s*```[\w+-]*\n(.*?)\n?```\s*$", code, re.DOTALL)
    return m.group(1) if m else code


def match_trailing_newline(new: str, original: str) -> str:
    new = new.rstrip("\n")
    return new + ("\n" if original.endswith("\n") else "")


def parses(code: str) -> str | None:
    """None if `code` parses, else the SyntaxError message."""
    try:
        ast.parse(strip_magics(code))
    except SyntaxError as e:
        return f"{e.msg} (line {e.lineno})"
    return None


def _clip(text: str, n: int = CHUNK_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _last(sym: str) -> str:
    return re.sub(r"\(.*\)$", "", sym).split(".")[-1].lstrip("@")


def match_verdict(symbol: str | None, verdicts: dict[str, DiffVerdict]) -> DiffVerdict | None:
    """Map a symbol the model reported to a verdict: exact, then same last component."""
    if not symbol:
        return None
    if symbol in verdicts:
        return verdicts[symbol]
    cands = [v for k, v in verdicts.items() if _last(k) == _last(symbol)]
    return cands[0] if len(cands) == 1 else None


def update_pins(
    source: str, targets: dict[str, tuple[str, str]]
) -> tuple[str, list[tuple[str, str, str]]]:
    """Rewrite version specifiers that exclude the target. `targets`: pypi_name -> (lib, version).

    Only install commands and `name==x` requirement lines are touched. Returns the new source and
    (lib, old_spec, new_version) tuples.
    """
    changes: list[tuple[str, str, str]] = []
    out: list[str] = []
    for line in source.split("\n"):
        is_install = bool(_INSTALL_LINE.search(line))
        is_req = bool(re.match(r"^\s*[A-Za-z][\w.-]*\s*==\s*\d", line))
        if is_install or is_req:
            for pypi, (lib, version) in targets.items():
                pat = re.compile(
                    rf"(?<![\w.-]){re.escape(pypi)}(?P<extras>\[[^\]]*\])?\s*"
                    r"(?P<spec>(?:==|<=|>=|~=|!=|<|>)\s*[0-9][\w.*+!-]*"
                    r"(?:\s*,\s*(?:==|<=|>=|~=|!=|<|>)\s*[0-9][\w.*+!-]*)*)"
                )

                def sub(m: re.Match, lib=lib, version=version, pypi=pypi) -> str:
                    spec = re.sub(r"\s+", "", m["spec"])
                    try:
                        if SpecifierSet(spec).contains(version):
                            return m.group(0)
                    except InvalidSpecifier:
                        return m.group(0)
                    changes.append((lib, spec, version))
                    return f"{pypi}{m['extras'] or ''}=={version}"

                line = pat.sub(sub, line)
        out.append(line)
    return "\n".join(out), changes


def add_packages_to_install(source: str, packages: list[str]) -> tuple[str, bool]:
    """Append `packages` to the first simple pip-install line. False if there is none."""
    lines = source.split("\n")
    for i, line in enumerate(lines):
        if _INSTALL_LINE.search(line) and not re.search(r"[&|;#]", line):
            have = set(re.findall(r"[A-Za-z][\w.-]*", line.lower()))
            add = [p for p in packages if p.lower() not in have]
            if add:
                lines[i] = line.rstrip() + " " + " ".join(add)
            return "\n".join(lines), True
    return source, False


# --- prompt -------------------------------------------------------------------------------


def build_prompt(
    block: CodeBlock,
    libs: dict[str, tuple[str | None, str]],
    verdicts: list[DiffVerdict],
    chunks: list[Chunk],
    syntax_error: str | None = None,
) -> str:
    versions = "; ".join(f"{lib} {old or '?'} -> {new}" for lib, (old, new) in libs.items())
    parts = [f"Update this code for: {versions}."]
    if verdicts:
        lines = [
            f"- {v.symbol}: {v.verdict}"
            + (f" -> use `{v.replacement}`" if v.replacement else "")
            + f" [evidence: {', '.join(v.evidence_chunk_ids)}]"
            for v in verdicts
        ]
        parts.append("Evidence-backed library changes to apply:\n" + "\n".join(lines))
        parts.append(
            "EVIDENCE:\n"
            + "\n".join(f"[{c.id}] {_clip(c.text)}" for c in chunks[:MAX_EVIDENCE_CHUNKS])
        )
    else:
        parts.append(
            "Fix anything that no longer works in the newer versions, using your knowledge of "
            "their breaking changes."
        )
    if block.last_error:
        parts.append(f"The current code fails with: {block.last_error}")
    parts.append(
        "The block runs after the earlier blocks of the same tutorial in one shared session, so "
        "keep variable names and definitions other blocks rely on."
    )
    if syntax_error:
        parts.append(f"Your previous attempt had a syntax error: {syntax_error}. Fix it.")
    parts.append(
        'Return JSON: {"code": <the full updated code, no fences>, "changes": [{"symbol": '
        "<the library symbol you changed, exactly as in the list above if applicable>, "
        '"description": <one line>}]}.\n\nCODE:\n' + block.source
    )
    return "\n\n".join(parts)


# --- node ---------------------------------------------------------------------------------


def make_rewrite(
    llm: LLM | None = None, flags: PipelineFlags | None = None
) -> Callable[[GraphState], dict]:
    holder = {"llm": llm}

    def get_llm() -> LLM:
        if holder["llm"] is None:
            holder["llm"] = LLM.from_env()
        return holder["llm"]

    def rewrite(state: GraphState) -> dict:
        f = flags or PipelineFlags()
        cited_mode = f.retrieval_enabled
        libs = {
            lib: (state.old_versions.get(lib), ver) for lib, ver in state.target_versions.items()
        }
        verdicts = {v.symbol: v for v in state.verdicts}
        chunk_by_id = {c.id: c for cs in state.evidence.values() for c in cs}
        warnings = list(state.warnings)
        entries = list(state.change_log)
        blocks = list(state.blocks)
        # Symbols per block come from ONE sequential analysis (blocks share a namespace), computed
        # on the pre-rewrite code so every block is judged against the same snapshot.
        used_by_block = analyze_blocks(state.blocks).used

        for i, b in enumerate(blocks):
            if b.lang != "python":
                continue
            used = used_by_block.get(b.id, set())
            mine = [v for k, v in verdicts.items() if k in used]
            if state.loop_count == 0:
                if cited_mode and not mine:
                    continue
            elif b.status != "failing":
                continue
            evidence_ids = list(dict.fromkeys(i for v in mine for i in v.evidence_chunk_ids))
            chunks = [chunk_by_id[i] for i in evidence_ids if i in chunk_by_id]

            new_code, syntax_err = None, None
            for _attempt in range(2):
                try:
                    out = get_llm().structured(
                        build_prompt(b, libs, mine if cited_mode else [], chunks, syntax_err),
                        RewriteOut,
                        system=SYSTEM,
                    )
                except LLMError as e:
                    warnings.append(f"rewrite {b.id}: LLM failed ({e}); block kept")
                    break
                code = match_trailing_newline(strip_fences(out.code), b.source)
                syntax_err = parses(code)
                if syntax_err is None:
                    new_code = (code, out.changes)
                    break
            if new_code is None:
                if syntax_err:
                    warnings.append(
                        f"rewrite {b.id}: unparseable output ({syntax_err}); block kept"
                    )
                continue
            code, changes = new_code
            if code == b.source:
                continue

            block_entries: list[ChangeLogEntry] = []
            if cited_mode:
                by_sym = {v.symbol: v for v in mine}
                for ch in changes:
                    v = match_verdict(ch.symbol, by_sym)
                    if v is None and len(mine) == 1:
                        v = mine[0]
                    if v is None:
                        warnings.append(f"rewrite {b.id}: unattributed change dropped: {ch.symbol}")
                        continue
                    block_entries.append(
                        ChangeLogEntry(
                            block_id=b.id,
                            symbol=v.symbol,
                            description=ch.description or f"{v.symbol}: {v.verdict}",
                            evidence_chunk_ids=v.evidence_chunk_ids,
                            source_urls=sorted(
                                {
                                    chunk_by_id[c].source_url
                                    for c in v.evidence_chunk_ids
                                    if c in chunk_by_id
                                }
                            ),
                        )  # fmt: skip
                    )
                if not block_entries:
                    warnings.append(f"rewrite {b.id}: change without cited evidence rejected")
                    continue
            else:
                block_entries = [
                    ChangeLogEntry(
                        block_id=b.id,
                        kind="llm_prior",
                        symbol=ch.symbol,
                        description=ch.description or "edited from model knowledge (uncited)",
                    )  # fmt: skip
                    for ch in changes
                ] or [
                    ChangeLogEntry(
                        block_id=b.id,
                        kind="llm_prior",
                        description="edited from model knowledge (uncited)",
                    )  # fmt: skip
                ]
            entries = [e for e in entries if e.block_id != b.id] + block_entries
            blocks[i] = b.model_copy(
                update={"source": code, "status": "pending", "last_error": None}
            )

        # deterministic: version pins in install/requirements blocks
        targets = {
            LIBRARIES[lib].pypi_name: (lib, ver) for lib, ver in state.target_versions.items()
        }
        for i, b in enumerate(blocks):
            if b.lang in SHELL_BLOCK_LANGS:
                new_src, pins = update_pins(b.source, targets)
                if pins:
                    blocks[i] = b.model_copy(update={"source": new_src})
                    for lib, spec, ver in pins:
                        pypi = LIBRARIES[lib].pypi_name
                        entries.append(
                            ChangeLogEntry(
                                block_id=b.id,
                                kind="version_pin",
                                symbol=lib,
                                description=f"install pin {pypi}{spec} -> {pypi}=={ver} (target)",
                                source_urls=[f"https://pypi.org/project/{pypi}/{ver}/"],
                            )  # fmt: skip
                        )

        # deterministic: packages the rewritten code now imports but the tutorial never installed
        after = extra_packages_for(blocks)
        new_pkgs = [p for p in after if p not in state.extra_packages]
        if new_pkgs:
            introduced = [
                e for e in entries if e.kind in ("api_change", "llm_prior") and e.block_id in {
                    b.id
                    for b in blocks
                    if b.lang == "python" and set(extra_packages_for([b])) & set(new_pkgs)
                }
            ]  # fmt: skip
            ev = list(dict.fromkeys(i for e in introduced for i in e.evidence_chunk_ids))
            for i, b in enumerate(blocks):
                if b.lang in SHELL_BLOCK_LANGS and _INSTALL_LINE.search(b.source):
                    new_src, ok = add_packages_to_install(b.source, new_pkgs)
                    if ok and new_src != b.source:
                        blocks[i] = b.model_copy(update={"source": new_src})
                        entries.append(
                            ChangeLogEntry(
                                block_id=b.id,
                                kind="dependency" if ev else "llm_prior",
                                description=f"added required package(s): {', '.join(new_pkgs)}",
                                evidence_chunk_ids=ev,
                                source_urls=sorted({u for e in introduced for u in e.source_urls}),
                            )  # fmt: skip
                        )
                    break
            else:
                warnings.append(
                    f"rewritten code needs {new_pkgs} but the tutorial has no install block"
                )

        return {
            "blocks": blocks,
            "change_log": entries,
            "warnings": warnings,
            "extra_packages": sorted(set(state.extra_packages) | set(after)),
        }

    return rewrite


rewrite = make_rewrite()

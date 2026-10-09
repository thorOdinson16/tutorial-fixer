"""Update the explanation text so it agrees with the code changes (opt-in: `update_prose`).

Until now prose was never touched. This stage edits it under strict rules:

  * Only sentences that mention a library version, or an API the code edits actually changed, are
    even shown to the model (no whole-document rewriting; a tutorial with nothing to fix costs no
    LLM call).
  * The model returns replacements for individual sentences, not a new document.
  * Every replacement is checked before it is applied: it must be tied to a changed symbol or a
    library version, stay close to the original (few words changed, similar length), keep links and
    backticks intact, introduce no number that is not a target version or part of the new API name,
    and match exactly one place in the prose. Anything else is dropped and logged.
  * Edits are applied by exact string replacement, so headings, links, code fences and every
    sentence that was not edited stay byte-for-byte identical.

Each applied edit is recorded (`state.prose_edits`) with its reason and the symbol or version that
justified it, and appears in the change log.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from trf.config import PipelineFlags
from trf.llm import LLM, LLMError
from trf.state import GraphState, ProseEdit

MAX_CANDIDATES = 25
MIN_SIMILARITY = 0.5
LENGTH_RANGE = (0.5, 1.8)
_VERSION = re.compile(r"(?<![\w.])v?\d+(?:\.\d+){1,3}(?!\w)(?!\.\d)")  # a trailing "." is fine
_NUMBER = re.compile(r"\d+")
_URL = re.compile(r"\]\(([^)\s]+)\)|https?://[^\s)>\]]+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'(\[])")
LIB_WORDS = {"sklearn": "scikit-learn"}  # how prose names a library whose import name differs

SYSTEM = (
    "You maintain the explanatory text of a Python tutorial whose code was just updated for newer "
    "library versions. Fix only text that has become FALSE because of the listed changes. "
    "Never rewrite for style, never add information, never touch text about things that did not "
    "change."
)


class ProseEditOut(BaseModel):
    id: int
    new: str
    reason: str = ""
    linked: str = ""  # the changed symbol, or "version"


class ProseOut(BaseModel):
    edits: list[ProseEditOut] = Field(default_factory=list)


def sentences(segment_text: str) -> list[str]:
    """Candidate sentences of one prose segment (front matter, headings, tables are skipped)."""
    out: list[str] = []
    in_front = False
    for i, line in enumerate(segment_text.split("\n")):
        s = line.strip()
        if i == 0 and s == "---":
            in_front = True
            continue
        if in_front:
            in_front = s != "---"
            continue
        if not s or s[0] in "#|<>" or s.startswith("```") or s.startswith("!["):
            continue
        out += [p.strip() for p in _SENT_SPLIT.split(s) if p.strip()]
    return out


def _tail(symbol: str) -> str:
    return re.sub(r"\(.*\)$", "", symbol).split(".")[-1].lstrip("@")


def changed_symbols(state: GraphState) -> dict[str, str]:
    """tail name -> description of the change, for symbols whose code was actually edited."""
    out: dict[str, str] = {}
    edited = {b.id for b in state.blocks if b.source != b.original_source}
    live: set[str] = set()  # symbols of change entries whose block really was edited
    for e in state.change_log:
        if e.kind in ("api_change", "llm_prior") and e.symbol and e.block_id in edited:
            out.setdefault(_tail(e.symbol), e.description)
            live.add(e.symbol)
    for v in state.verdicts:
        if v.symbol in live:
            repl = f" -> {v.replacement}" if v.replacement else ""
            out[_tail(v.symbol)] = f"{v.symbol}: {v.verdict}{repl}"
    return {k: d for k, d in out.items() if len(k) >= 3}


def _mentions_symbol(sentence: str, tail: str) -> bool:
    return bool(re.search(rf"(?<![\w]){re.escape(tail)}(?![\w])", sentence))


def _mentions_version(sentence: str, libraries: list[str]) -> bool:
    if not _VERSION.search(sentence) and not re.search(r"\bversion\s+\d", sentence, re.I):
        return False
    low = sentence.lower()
    names = [LIB_WORDS.get(lib, lib).lower() for lib in libraries]
    return any(n in low for n in names) or "version" in low


def candidates(state: GraphState) -> list[tuple[int, str, str]]:
    """(segment index, sentence, why) for sentences worth showing to the model."""
    syms = changed_symbols(state)
    out: list[tuple[int, str, str]] = []
    for i, seg in enumerate(state.segments):
        if seg.kind != "prose":
            continue
        for s in sentences(seg.text):
            if seg.text.count(s) != 1:
                continue
            tail = next((t for t in syms if _mentions_symbol(s, t)), None)
            if tail:
                out.append((i, s, tail))
            elif _mentions_version(s, state.libraries):
                out.append((i, s, "version"))
    return out[:MAX_CANDIDATES]


def _digits(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def validate_edit(
    old: str, new: str, linked: str, allowed_numbers: set[str], symbols: dict[str, str]
) -> str | None:
    """None if the edit is acceptable, else the reason it was rejected."""
    new = new.strip()
    if not new or new == old:
        return "no change"
    ratio = len(new) / max(len(old), 1)
    if not LENGTH_RANGE[0] <= ratio <= LENGTH_RANGE[1]:
        return "length changed too much"
    if difflib.SequenceMatcher(None, old, new).ratio() < MIN_SIMILARITY:
        return "rewritten, not edited"
    if sorted(_URL.findall(old)) != sorted(_URL.findall(new)):
        return "links changed"
    if new.count("`") % 2 or "```" in new:
        return "unbalanced backticks"
    tied = linked == "version" or any(_mentions_symbol(old, t) for t in symbols)
    if not tied:
        return "not tied to a changed symbol or a version"
    extra = _digits(new) - _digits(old) - allowed_numbers
    if extra:
        return f"introduces numbers {sorted(extra)}"
    return None


def make_prose(
    llm: LLM | None = None, flags: PipelineFlags | None = None
) -> Callable[[GraphState], dict]:
    holder = {"llm": llm}

    def prose(state: GraphState) -> dict:
        f = flags or PipelineFlags()
        if not f.update_prose or not state.segments:
            return {}
        cands = candidates(state)
        if not cands:
            return {}
        if holder["llm"] is None:
            holder["llm"] = LLM.from_env()
        syms = changed_symbols(state)
        versions = "; ".join(
            f"{LIB_WORDS.get(lib, lib)} {state.old_versions.get(lib, '?')} -> {ver}"
            for lib, ver in sorted(state.target_versions.items())
        )
        changes = "\n".join(f"- {d}" for d in syms.values()) or "- (none)"
        listing = "\n".join(f"[{n}] {s}" for n, (_, s, _) in enumerate(cands))
        prompt = (
            f"Library versions: {versions}\n\nCode changes that were made:\n{changes}\n\n"
            f"SENTENCES (numbered):\n{listing}\n\n"
            'Return JSON {"edits": [{"id": <number>, "new": <the sentence with the wrong words '
            'replaced>, "reason": <short>, "linked": <the changed symbol name, or "version">}]}. '
            "Include ONLY sentences that are now wrong (an outdated library version, or an API "
            "the code no longer uses). Change as few words as possible; keep backticks and links. "
            'If nothing is wrong return {"edits": []}.'
        )
        try:
            out = holder["llm"].structured(prompt, ProseOut, system=SYSTEM)
        except LLMError as e:
            return {"warnings": [*state.warnings, f"prose: LLM failed ({e})"]}

        allowed = set()
        for ver in state.target_versions.values():
            allowed |= _digits(ver)
        for d in syms.values():
            allowed |= _digits(d.split(":", 1)[-1].split("->")[-1])
        segments = list(state.segments)
        applied: list[ProseEdit] = []
        warnings = list(state.warnings)
        for e in out.edits:
            if not 0 <= e.id < len(cands):
                continue
            idx, old, why = cands[e.id]
            reason = validate_edit(old, e.new, e.linked or why, allowed, syms)
            seg = segments[idx]
            if reason is None and seg.text.count(old) != 1:
                reason = "does not match exactly one place"
            if reason:
                warnings.append(f"prose: dropped edit ({reason}): {old[:60]!r}")
                continue
            segments[idx] = seg.model_copy(update={"text": seg.text.replace(old, e.new.strip(), 1)})
            applied.append(
                ProseEdit(
                    segment=idx,
                    old=old,
                    new=e.new.strip(),
                    reason=e.reason or "agrees with the code change",
                    linked=e.linked or why,
                )
            )
        if not applied:
            return {"warnings": warnings} if warnings != state.warnings else {}
        return {
            "segments": segments,
            "prose_edits": [*state.prose_edits, *applied],
            "warnings": warnings,
        }

    return prose


prose = make_prose()

"""Classify each used symbol: unchanged | renamed | signature_changed | removed | behavior_changed.

Symbols go to the LLM in small batches per library (6 each): reasoning models pay a large fixed
thinking cost per call, so per-symbol calls are far too slow, but one giant prompt overflows the
context window. Every recorded verdict must cite evidence chunk ids that were actually shown to
the model; verdicts that cite nothing valid are dropped, never kept as claims.
A symbol with no recorded verdict means "no change claimed" (unchanged).
In loop passes (loop_count > 0) only the symbols `query_rewrite` derived from sandbox errors are
classified, and their verdicts are added to the existing ones.

Without retrieval (the LLM-only baseline) there is nothing to cite, so this stage is skipped.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from trf.config import PipelineFlags
from trf.llm import LLM, LLMError
from trf.state import Chunk, DiffVerdict, GraphState, Symbol, VerdictKind

MAX_SYMBOLS = 30
SYMBOLS_PER_CALL = 6  # small batches: reasoning models need prompt + thinking + answer to fit
CHUNKS_PER_SYMBOL = 3
CHUNK_CHARS = 600
SKIP_KINDS = {"cli", "module"}

SYSTEM = (
    "You are a precise migration analyst for Python libraries. Use ONLY the evidence chunks "
    "provided; never rely on memory. If the evidence does not show a change for a symbol, omit it."
)

VERDICT_HELP = """\
- renamed: the symbol exists under a new name (replacement = new name)
- signature_changed: same symbol, but arguments/keywords/config keys changed
  (replacement = new form)
- removed: gone, or replaced by a different construct
  (replacement = what to use instead, or null)
- behavior_changed: same API, different behaviour/defaults that can break existing code
- Omit symbols that are unchanged."""


_KIND_SUFFIX = re.compile(
    r"\s*[\(\[](?:function|class|method|attribute|module|cli|config_key|argument)[\)\]]\s*$", re.I
)


def clean_symbol(raw: str, known: set[str]) -> str:
    """Models often echo the prompt's `name (kind)` line; map that back to the bare symbol."""
    s = raw.strip().strip("`\"'")
    if s in known:
        return s
    stripped = _KIND_SUFFIX.sub("", s).strip().strip("`\"'")
    if stripped in known:
        return stripped
    lowered = {k.lower(): k for k in known}
    return lowered.get(stripped.lower(), s)


class VerdictOut(BaseModel):
    symbol: str
    verdict: VerdictKind
    replacement: str | None = None
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class DiffBatch(BaseModel):
    verdicts: list[VerdictOut] = Field(default_factory=list)


def _clip(text: str, n: int = CHUNK_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def symbol_terms(names: list[str]) -> list[str]:
    """Strings to look for in evidence text, most specific first: `pandas.datetime`, `datetime`."""
    full, short = [], []
    for name in names:
        dotted = re.sub(r"\(.*\)$", "", name)
        arg = re.search(r"\((\w+)\)$", name)
        full.append(dotted)
        short.append(dotted.split(".", 1)[-1])
        short.append(dotted.split(".")[-1])
        if arg:
            short.append(arg[1])
    return list(dict.fromkeys(t for t in [*full, *short] if len(t) >= 3))


def snippet(text: str, terms: list[str], n: int = CHUNK_CHARS, max_windows: int = 3) -> str:
    """Whitespace-normalised text cut to ~n chars around where `terms` are mentioned.

    A head-clip loses the one sentence that matters when a long changelog chunk lists many
    removals. With no match (or a short text) this is the plain head-clip.
    """
    t = " ".join(text.split())
    if len(t) <= n:
        return t
    hits: list[int] = []
    for term in terms:
        m = re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", t, re.I)
        if m and not any(abs(m.start() - h) < n // 4 for h in hits):
            hits.append(m.start())
        if len(hits) == max_windows:
            break
    if not hits:
        return t[: n - 1] + "…"
    w = n // len(hits)
    parts, floor = [], 0
    for pos in sorted(hits):
        start = max(floor, pos - w // 3)
        end = min(len(t), start + w)
        parts.append(("…" if start > 0 else "") + t[start:end] + ("…" if end < len(t) else ""))
        floor = end
    return " ".join(parts)


def build_prompt(
    library: str, old: str | None, target: str, symbols: list[Symbol], chunks: list[Chunk],
    per_symbol: dict[str, list[str]], observed: dict[str, str] | None = None,
) -> str:  # fmt: skip
    terms = symbol_terms([s.name for s in symbols])
    ev = "\n".join(f"[{c.id}] {snippet(c.text, terms)}" for c in chunks)
    syms = "\n".join(
        f"- {s.name} ({s.kind}); candidate evidence: {', '.join(per_symbol[s.name])}"
        for s in symbols
    )
    seen = "".join(
        f"- {n}: running the tutorial failed with: {err}\n" for n, err in (observed or {}).items()
    )
    seen = (
        f"OBSERVED FAILURES (this symbol is known to break on {target}):\n{seen}\n" if seen else ""
    )
    return (
        f"Library: {library}. The tutorial was written for {old or 'an older version'}; "
        f"the target version is {target}.\n\nEVIDENCE CHUNKS (cite by id):\n{ev}\n\n"
        f"SYMBOLS USED IN THE TUTORIAL:\n{syms}\n\n{seen}"
        f"Verdict meanings:\n{VERDICT_HELP}\n\n"
        "Return the symbols that CHANGED. For each: the exact symbol string from the list (just "
        "the name, without the parenthesised kind that follows it), the "
        "verdict, the replacement (or null), the chunk ids that state the change (ids copied "
        "exactly from the evidence list), and a confidence in [0, 1]."
    )


def make_diff(
    llm: LLM | None = None, flags: PipelineFlags | None = None, min_confidence: float = 0.5
) -> Callable[[GraphState], dict]:
    holder = {"llm": llm}

    def diff(state: GraphState) -> dict:
        f = flags or PipelineFlags()
        if not f.retrieval_enabled:
            return {}
        verdicts: list[DiffVerdict] = []
        warnings: list[str] = []
        only = set(state.fresh_symbols) if state.loop_count > 0 else None
        errors = {
            n: (b.last_error or "")[:300]
            for b in state.blocks
            for n in state.error_symbols.get(b.id, [])
            if b.status == "failing"
        }
        for lib in state.libraries:
            target = state.target_versions.get(lib)
            syms = [
                s for s in state.symbols
                if s.library == lib and s.kind not in SKIP_KINDS and state.evidence.get(s.name)
                and (only is None or s.name in only)
            ][:MAX_SYMBOLS]  # fmt: skip
            if not target or not syms:
                continue
            known = {s.name for s in syms}
            if holder["llm"] is None:
                holder["llm"] = LLM.from_env()
            for start in range(0, len(syms), SYMBOLS_PER_CALL):
                batch = syms[start : start + SYMBOLS_PER_CALL]
                per_symbol = {
                    s.name: [c.id for c in state.evidence[s.name][:CHUNKS_PER_SYMBOL]]
                    for s in batch
                }
                by_id: dict[str, Chunk] = {}
                for s in batch:
                    for c in state.evidence[s.name][:CHUNKS_PER_SYMBOL]:
                        by_id.setdefault(c.id, c)
                chunks = list(by_id.values())
                shown = set(by_id)
                try:
                    out = holder["llm"].structured(
                        build_prompt(
                            lib,
                            state.old_versions.get(lib),
                            target,
                            batch,
                            chunks,
                            per_symbol,
                            {s.name: errors[s.name] for s in batch if errors.get(s.name)},
                        ),
                        DiffBatch,
                        system=SYSTEM,
                    )
                except LLMError as e:
                    warnings.append(f"diff: LLM failed for {[s.name for s in batch]}: {e}")
                    continue
                in_batch = {s.name for s in batch}
                for v in out.verdicts:
                    v = v.model_copy(update={"symbol": clean_symbol(v.symbol, in_batch)})
                    if v.symbol not in in_batch or v.symbol not in known:
                        warnings.append(f"diff: dropped verdict for unknown symbol {v.symbol!r}")
                        continue
                    if v.verdict == "unchanged":
                        continue
                    ids = [i for i in dict.fromkeys(v.evidence_chunk_ids) if i in shown]
                    if not ids:
                        warnings.append(
                            f"diff: dropped uncited verdict for {v.symbol} ({v.verdict})"
                        )
                        continue
                    if v.confidence < min_confidence:
                        warnings.append(f"diff: dropped low-confidence verdict for {v.symbol}")
                        continue
                    verdicts.append(
                        DiffVerdict(
                            symbol=v.symbol,
                            verdict=v.verdict,
                            replacement=v.replacement,
                            evidence_chunk_ids=ids,
                            confidence=v.confidence,
                        )  # fmt: skip
                    )
        if only is not None:
            new = {v.symbol for v in verdicts}
            verdicts = [v for v in state.verdicts if v.symbol not in new] + verdicts
        return {"verdicts": verdicts, "warnings": [*state.warnings, *warnings]}

    return diff


diff = make_diff()

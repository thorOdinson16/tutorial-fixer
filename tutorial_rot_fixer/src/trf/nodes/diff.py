"""Classify each used symbol: unchanged | renamed | signature_changed | removed | behavior_changed.

Symbols go to the LLM in small batches per library (6 each): reasoning models pay a large fixed
thinking cost per call, so per-symbol calls are far too slow, but one giant prompt overflows the
context window. Every recorded verdict must cite evidence chunk ids that were actually shown to
the model; verdicts that cite nothing valid are dropped, never kept as claims.
A symbol with no recorded verdict means "no change claimed" (unchanged).

Without retrieval (the LLM-only baseline) there is nothing to cite, so this stage is skipped.
"""

from __future__ import annotations

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


def build_prompt(
    library: str, old: str | None, target: str, symbols: list[Symbol], chunks: list[Chunk],
    per_symbol: dict[str, list[str]],
) -> str:  # fmt: skip
    ev = "\n".join(f"[{c.id}] {_clip(c.text)}" for c in chunks)
    syms = "\n".join(
        f"- {s.name} ({s.kind}); candidate evidence: {', '.join(per_symbol[s.name])}"
        for s in symbols
    )
    return (
        f"Library: {library}. The tutorial was written for {old or 'an older version'}; "
        f"the target version is {target}.\n\nEVIDENCE CHUNKS (cite by id):\n{ev}\n\n"
        f"SYMBOLS USED IN THE TUTORIAL:\n{syms}\n\n"
        f"Verdict meanings:\n{VERDICT_HELP}\n\n"
        "Return the symbols that CHANGED. For each: the exact symbol string from the list, the "
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
        for lib in state.libraries:
            target = state.target_versions.get(lib)
            syms = [
                s for s in state.symbols
                if s.library == lib and s.kind not in SKIP_KINDS and state.evidence.get(s.name)
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
                            lib, state.old_versions.get(lib), target, batch, chunks, per_symbol
                        ),
                        DiffBatch,
                        system=SYSTEM,
                    )
                except LLMError as e:
                    warnings.append(f"diff: LLM failed for {[s.name for s in batch]}: {e}")
                    continue
                for v in out.verdicts:
                    if v.symbol not in {s.name for s in batch} or v.symbol not in known:
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
        return {"verdicts": verdicts, "warnings": [*state.warnings, *warnings]}

    return diff


diff = make_diff()

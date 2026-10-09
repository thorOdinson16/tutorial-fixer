"""Typed state shared by every graph node."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

BlockStatus = Literal["pending", "passing", "failing", "unresolved"]
SymbolKind = Literal[
    "function", "class", "method", "attribute", "module", "cli", "config_key", "argument"
]
DocType = Literal["docs", "changelog", "migration", "release"]
VerdictKind = Literal["unchanged", "renamed", "signature_changed", "removed", "behavior_changed"]


class SessionStmt(BaseModel):
    """One statement of an interactive session (`>>> code`) and the output lines under it."""

    code: list[str]
    output: list[str] = Field(default_factory=list)


class CodeBlock(BaseModel):
    id: str
    lang: str = "python"
    source: str
    original_source: str
    status: BlockStatus = "pending"
    last_error: str | None = None
    session: list[SessionStmt] | None = None  # set when the fence was a `>>>` transcript

    @classmethod
    def new(cls, id: str, source: str, lang: str = "python") -> CodeBlock:
        return cls(id=id, lang=lang, source=source, original_source=source)


class Symbol(BaseModel):
    name: str
    kind: SymbolKind = "function"
    library: str
    old_version: str | None = None
    first_seen_in_block: str | None = None


class Chunk(BaseModel):
    id: str
    text: str
    library: str
    version_introduced: str | None = None
    version_removed: str | None = None
    symbol: str | None = None
    doc_type: DocType = "docs"
    source_url: str


def _require_evidence(ids: list[str]) -> list[str]:
    if not ids:
        raise ValueError(
            "evidence_chunk_ids must cite at least one retrieved chunk (no uncited claims)"
        )
    return ids


class DiffVerdict(BaseModel):
    symbol: str
    verdict: VerdictKind
    replacement: str | None = None
    evidence_chunk_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)

    _cited = field_validator("evidence_chunk_ids")(_require_evidence)


ChangeKind = Literal["api_change", "version_pin", "dependency", "llm_prior", "defect_fix"]


class ChangeLogEntry(BaseModel):
    """One edit, with the evidence that justifies it.

    kind=api_change / dependency must cite retrieved chunks; version_pin cites the PyPI page of
    the resolved version; llm_prior is ONLY for the no-retrieval baseline and is labelled as
    uncited in the change log.
    """

    block_id: str
    kind: ChangeKind = "api_change"
    symbol: str | None = None
    description: str
    error: str | None = None  # defect_fix: the error the repair removed
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cited(self) -> ChangeLogEntry:
        if self.kind in ("api_change", "dependency") and not self.evidence_chunk_ids:
            raise ValueError("evidence_chunk_ids must cite at least one retrieved chunk")
        if self.kind == "defect_fix" and not self.error:
            raise ValueError("defect_fix entries must quote the error they removed")
        if self.kind == "version_pin" and not self.source_urls:
            raise ValueError("version_pin entries must cite a source URL")
        return self


class RunResult(BaseModel):
    block_id: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    traceback: str | None = None
    timed_out: bool = False
    passed_assertions: bool | None = None  # None = no assertions defined
    warnings: list[dict[str, str]] = Field(default_factory=list)  # category, message, line

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.passed_assertions is not False


class ProseEdit(BaseModel):
    """One edited sentence of the explanation text, with what justified it."""

    segment: int  # index into GraphState.segments
    old: str
    new: str
    reason: str
    linked: str  # the changed symbol, or "version"


class Segment(BaseModel):
    """Lossless slice of the tutorial: prose is kept verbatim, code carries its fence lines."""

    kind: Literal["prose", "code"]
    text: str
    block_id: str | None = None
    fence_open: str = ""
    fence_close: str = ""


class Assertion(BaseModel):
    """Optional per-block pass criteria beyond 'exits 0'."""

    stdout_contains: list[str] = Field(default_factory=list)
    snippet: str | None = None  # python run in the tutorial's namespace right after the block


class GraphState(BaseModel):
    tutorial_md: str = ""
    segments: list[Segment] = Field(default_factory=list)
    tutorial_date: date | None = None
    pins: dict[str, str] = Field(default_factory=dict)  # package -> specifier, e.g. "==1.3.5"
    libraries: list[str] = Field(default_factory=list)  # registered libs the tutorial uses
    extra_packages: list[str] = Field(default_factory=list)  # other third-party pip deps
    user_target_pins: dict[str, str] = Field(default_factory=dict)
    assertions: dict[str, Assertion] = Field(default_factory=dict)  # keyed by block id
    blocks: list[CodeBlock] = Field(default_factory=list)
    symbols: list[Symbol] = Field(default_factory=list)
    old_versions: dict[str, str] = Field(default_factory=dict)
    target_versions: dict[str, str] = Field(default_factory=dict)
    evidence: dict[str, list[Chunk]] = Field(default_factory=dict)
    verdicts: list[DiffVerdict] = Field(default_factory=list)
    run_results: list[RunResult] = Field(default_factory=list)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)
    warning_report: list[dict[str, str]] = Field(default_factory=list)  # warnings still emitted
    prose_edits: list[ProseEdit] = Field(default_factory=list)
    suggestions: dict[str, str] = Field(default_factory=dict)  # block id -> suggested definition
    protected_blocks: list[str] = Field(default_factory=list)  # work already; rewrite skips them
    old_outputs: dict[str, str] = Field(default_factory=dict)  # stdout of the original, old env
    error_symbols: dict[str, list[str]] = Field(default_factory=dict)  # failing block -> symbols
    fresh_symbols: list[str] = Field(default_factory=list)  # symbols found from errors this loop
    loop_count: int = 0
    max_loops: int = 4
    unresolved_block_ids: list[str] = Field(default_factory=list)
    output_md: str = ""
    change_log_md: str = ""
    warnings: list[str] = Field(default_factory=list)

    def failing_block_ids(self) -> list[str]:
        return [b.id for b in self.blocks if b.status == "failing"]

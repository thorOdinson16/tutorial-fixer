"""Final markdown + change log.

Prose is reproduced verbatim; only code-fence bodies are swapped for the (possibly rewritten)
blocks. Any block still failing here is marked `unresolved` with its last error: it is never
reported as passing. Blocks that were not executed (non-python) are reported as such.
"""

from __future__ import annotations

import difflib

from trf.nodes.diagnose import diagnose
from trf.state import ChangeLogEntry, CodeBlock, GraphState, Segment

STATUS_LABEL = {
    "passing": "passing",
    "failing": "failing",
    "unresolved": "UNRESOLVED",
    "pending": "not executed",
}


def render(segments: list[Segment], blocks: dict[str, CodeBlock]) -> str:
    out: list[str] = []
    for s in segments:
        if s.kind == "prose":
            out.append(s.text)
        else:
            b = blocks.get(s.block_id or "")
            out.append(s.fence_open + (b.source if b else s.text) + s.fence_close)
    return "".join(out)


def _evidence_line(e: ChangeLogEntry, urls: dict[str, str]) -> str:
    if e.kind == "llm_prior":
        return "uncited: made from model knowledge, no retrieval"
    if e.kind == "defect_fix":
        return f"no release note applies; removes the error: {(e.error or '')[:120]}"
    srcs = e.source_urls or sorted({urls[c] for c in e.evidence_chunk_ids if c in urls})
    cite = ", ".join(f"`{c}`" for c in e.evidence_chunk_ids)
    links = " · ".join(f"[source]({u})" for u in srcs)
    return " ".join(x for x in (f"evidence: {cite}" if cite else "", links) if x)


def _prose_section(state: GraphState) -> list[str]:
    if not state.prose_edits:
        return []
    lines = ["## Prose changes", ""]
    for e in state.prose_edits:
        lines += [
            f"- _{e.old}_ -> _{e.new}_",
            f"  - Why: {e.reason} (linked to `{e.linked}`).",
        ]
    return [*lines, ""]


def _suggestion_section(state: GraphState) -> list[str]:
    if not state.suggestions:
        return []
    lines = [
        "## Suggested fixes (not applied)",
        "",
        "These blocks fail because the tutorial is incomplete. A possible fix is shown; it is an "
        "assumption, so it was not applied.",
        "",
    ]
    for block_id, text in state.suggestions.items():
        lines += [f"- **{block_id}**: `{text}`"]
    return [*lines, ""]


def _output_comparison(state: GraphState, py: list[CodeBlock]) -> list[str]:
    """Blocks whose output now differs from what the ORIGINAL printed in its old environment.

    Only available when the precheck ran (`protect_working_blocks`). A difference is not
    necessarily wrong (a repr or dtype can change legitimately), but an edit that changes what a
    tutorial shows is exactly what a reader should look at.
    """
    if not state.old_outputs:
        return []
    now = {r.block_id: r.stdout for r in state.run_results if r.ok}
    comparable = [b.id for b in py if b.id in state.old_outputs and b.id in now]
    differ = [i for i in comparable if state.old_outputs[i].strip() != now[i].strip()]
    lines = [
        "## Output compared with the original",
        "",
        f"{len(comparable) - len(differ)} of {len(comparable)} comparable blocks print the same "
        "output as the original did in its old environment.",
    ]
    if differ:
        lines += [
            f"**Different output: {', '.join(differ)}.** Check these edits; the code runs but "
            "what it shows has changed.",
        ]
    skipped = len(py) - len(comparable)
    if skipped:
        lines.append(f"{skipped} block(s) could not be compared (failed before or after).")
    return [*lines, ""]


def build_change_log(state: GraphState, blocks: list[CodeBlock]) -> str:
    urls = {c.id: c.source_url for cs in state.evidence.values() for c in cs}
    lines = ["# Change log", ""]
    if state.target_versions:
        vers = ", ".join(
            f"{lib} {state.old_versions.get(lib, '?')} → {ver}"
            for lib, ver in sorted(state.target_versions.items())
        )
        lines += [f"Migrated: {vers}", ""]

    py = [b for b in blocks if b.lang == "python"]
    n_pass = sum(b.status == "passing" for b in py)
    lines += [
        f"Code blocks: {len(blocks)} total, {len(py)} python, **{n_pass}/{len(py)} passing**",
        "",
    ]

    lines += [
        "## Run status per code block",
        "",
        "| block | lang | status | detail |",
        "|---|---|---|---|",
    ]
    for b in blocks:
        detail = (b.last_error or "").replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(f"| {b.id} | {b.lang or '-'} | {STATUS_LABEL[b.status]} | {detail} |")
    lines.append("")

    lines += _output_comparison(state, py)

    lines += _prose_section(state)
    lines += ["## Changes", ""]
    if not state.change_log:
        lines += ["_No edits were made._", ""]
    for b in blocks:
        mine = [e for e in state.change_log if e.block_id == b.id]
        if not mine:
            continue
        lines.append(f"### Block {b.id}")
        for e in mine:
            what = f"`{e.symbol}`: " if e.symbol and e.kind != "version_pin" else ""
            lines.append(f"- [{e.kind}] {what}{e.description} — {_evidence_line(e, urls)}")
        diff = "".join(
            difflib.unified_diff(
                b.original_source.splitlines(keepends=True),
                b.source.splitlines(keepends=True),
                "original",
                "updated",
                n=1,
            )  # fmt: skip
        )
        if diff:
            lines += ["", "```diff", diff.rstrip("\n"), "```"]
        lines.append("")

    unresolved = [b for b in blocks if b.status == "unresolved"]
    if unresolved:
        lines += [
            "## Unresolved blocks",
            "",
            "These blocks still fail and need manual attention:",
            "",
        ]
        for b in unresolved:
            lines.append(f"- **{b.id}**: {b.last_error}")
            d = diagnose(b.last_error)
            if d:
                lines.append(f"  - Likely cause: {d.text}.")
        lines.append("")
    lines += _suggestion_section(state)
    if state.warnings:
        lines += ["## Pipeline warnings", ""] + [f"- {w}" for w in state.warnings] + [""]
    return "\n".join(lines)


def assemble(state: GraphState) -> dict:
    blocks = [
        b.model_copy(update={"status": "unresolved"}) if b.status == "failing" else b
        for b in state.blocks
    ]
    return {
        "blocks": blocks,
        "unresolved_block_ids": [b.id for b in blocks if b.status == "unresolved"],
        "output_md": render(state.segments, {b.id: b for b in blocks})
        if state.segments
        else state.tutorial_md,
        "change_log_md": build_change_log(state, blocks),
    }

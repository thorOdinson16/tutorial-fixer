"""FastAPI service: submit a tutorial, poll the job, read the per-block diff, download the fix."""

from __future__ import annotations

import inspect
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from trf.nodes.diagnose import diagnose
from trf.state import BlockStatus, ChangeKind, GraphState
from trf.webfetch import FetchError, fetch_markdown

STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("text/javascript", ".js")  # Windows registries sometimes say text/plain
mimetypes.add_type("text/css", ".css")
MAX_MARKDOWN_CHARS = 200_000
MAX_JOBS = 100
ERROR_CHARS = 500

Runner = Callable[..., GraphState]  # (state) or (state, on_stage=callable)
JobStatus = Literal["queued", "running", "done", "failed"]


# the opt-in stages, as the page offers them: (request key, PipelineFlags field, env override)
MODES: dict[str, tuple[str, str]] = {
    "protect": ("protect_working_blocks", "TRF_PROTECT_WORKING_BLOCKS"),
    "repair": ("repair_defects", "TRF_REPAIR_DEFECTS"),
    "prose": ("update_prose", "TRF_UPDATE_PROSE"),
    "warnings": ("fix_warnings", "TRF_FIX_WARNINGS"),
}
# planning numbers behind the estimate: measured on the 606-block hold-out run, rounded up
SECONDS_PER_BLOCK = 8.0
LEDGER_USD_PER_BLOCK = 0.0012


class JobRequest(BaseModel):
    """A tutorial to fix: Markdown pasted in, or the address of a web page (exactly one)."""

    markdown: str | None = Field(default=None, max_length=MAX_MARKDOWN_CHARS)
    url: str | None = Field(default=None, max_length=2000)
    max_loops: int = Field(default=4, ge=0, le=8)
    target_pins: dict[str, str] = Field(default_factory=dict)
    modes: dict[str, bool] = Field(default_factory=dict)  # keys of MODES; missing = service default

    @model_validator(mode="after")
    def _one_source(self) -> JobRequest:
        has_md = bool(self.markdown and self.markdown.strip())
        has_url = bool(self.url and self.url.strip())
        if has_md == has_url:
            raise ValueError("send either markdown or a url")
        unknown = set(self.modes) - set(MODES)
        if unknown:
            raise ValueError(f"unknown modes: {sorted(unknown)}")
        return self


class Preview(BaseModel):
    title: str
    markdown: str
    python_blocks: int
    libraries: list[str]
    est_seconds: int
    est_budget_usd: float


class JobCreated(BaseModel):
    id: str
    status: Literal["queued"] = "queued"


class BlockView(BaseModel):
    id: str
    lang: str
    status: BlockStatus
    last_error: str | None
    original_source: str
    source: str
    changed: bool
    output_matches: bool | None = None  # same stdout as the original in its old env (if known)
    diagnosis: str | None = None  # plain-language reason an unresolved block is not version rot
    downstream_of: str | None = None  # the earlier block whose failure probably caused this one


class ChangeView(BaseModel):
    block_id: str
    kind: ChangeKind
    symbol: str | None
    description: str
    evidence_chunk_ids: list[str]
    source_urls: list[str]
    cited: bool
    error: str | None = None  # defect_fix: the error the repair removed


class ProseView(BaseModel):
    old: str
    new: str
    reason: str
    linked: str


class Summary(BaseModel):
    total_python_blocks: int
    passing: int
    unresolved: int
    loops_used: int
    downstream: int = 0  # unresolved blocks that follow an earlier failure
    output_compared: int = 0
    output_same: int = 0


_DOWNSTREAM = re.compile(r" *\[after earlier failure in (b\d+); may be a downstream effect\]")


class JobResult(BaseModel):
    original_md: str
    output_md: str
    change_log_md: str
    summary: Summary
    blocks: list[BlockView]
    changes: list[ChangeView]
    warnings: list[str]
    migrated: dict[str, dict[str, str]]
    prose_edits: list[ProseView] = []
    suggestions: dict[str, str] = {}


class JobView(BaseModel):
    id: str
    status: JobStatus
    title: str = ""
    created: float = 0.0
    stage: str | None = None  # pipeline stage while running
    error: str | None = None
    result: JobResult | None = None


class JobSummary(BaseModel):
    id: str
    status: JobStatus
    title: str
    created: float
    passing: int | None = None
    total: int | None = None


@dataclass
class _Job:
    title: str = ""
    created: float = 0.0
    status: JobStatus = "queued"
    stage: str | None = None
    error: str | None = None
    result: JobResult | None = None


class _Jobs:
    """Thread-safe in-memory job table. Past MAX_JOBS, the oldest finished jobs are dropped."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._dir = Path(directory) if directory else None
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)
            for f in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    job = _Job(
                        d.get("title", ""),
                        d.get("created", f.stat().st_mtime),
                        d["status"],
                        d.get("stage"),
                        d.get("error"),
                        JobResult.model_validate(d["result"]) if d.get("result") else None,
                    )
                except (OSError, ValueError, KeyError):
                    continue
                if job.status in ("queued", "running"):  # the process died mid-job
                    job.status, job.error = "failed", "interrupted by a server restart"
                self._jobs[f.stem] = job

    def _save(self, job_id: str) -> None:
        if not self._dir:
            return
        j = self._jobs[job_id]
        data = {
            "title": j.title,
            "created": j.created,
            "status": j.status,
            "stage": j.stage,
            "error": j.error,
            "result": j.result.model_dump() if j.result else None,
        }
        tmp = self._dir / f"{job_id}.tmp"
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self._dir / f"{job_id}.json")

    def create(self, title: str = "") -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = _Job(title=title, created=time.time())
            self._save(job_id)
            for old_id in list(self._jobs):
                if len(self._jobs) <= MAX_JOBS:
                    break
                if self._jobs[old_id].status in ("done", "failed"):
                    del self._jobs[old_id]
                    if self._dir:
                        (self._dir / f"{old_id}.json").unlink(missing_ok=True)
        return job_id

    def set_stage(self, job_id: str, stage: str) -> None:
        with self._lock:
            self._jobs[job_id].stage = stage

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        error: str | None = None,
        result: JobResult | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status, job.error, job.result = status, error, result
            if status in ("done", "failed"):
                job.stage = None
            self._save(job_id)

    def get(self, job_id: str) -> JobView | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return JobView(
                id=job_id,
                title=job.title,
                created=job.created,
                status=job.status,
                stage=job.stage,
                error=job.error,
                result=job.result,
            )

    def summaries(self) -> list[JobSummary]:
        """Newest first, without the (large) results."""
        with self._lock:
            rows = [
                JobSummary(
                    id=job_id,
                    status=j.status,
                    title=j.title,
                    created=j.created,
                    passing=j.result.summary.passing if j.result else None,
                    total=j.result.summary.total_python_blocks if j.result else None,
                )
                for job_id, j in self._jobs.items()
            ]
        return sorted(rows, key=lambda r: r.created, reverse=True)


def build_result(original_md: str, final: GraphState) -> JobResult:
    """Convert the finished pipeline state into the API payload. Only status 'passing' counts."""
    py_blocks = [b for b in final.blocks if b.lang == "python"]
    now = {r.block_id: r.stdout for r in final.run_results if r.ok}
    matches: dict[str, bool] = {
        b.id: final.old_outputs[b.id].strip() == now[b.id].strip()
        for b in py_blocks
        if b.id in final.old_outputs and b.id in now
    }
    return JobResult(
        original_md=original_md,
        output_md=final.output_md,
        change_log_md=final.change_log_md,
        summary=Summary(
            total_python_blocks=len(py_blocks),
            passing=sum(b.status == "passing" for b in py_blocks),
            unresolved=sum(b.status == "unresolved" for b in py_blocks),
            downstream=sum(
                b.status == "unresolved" and "downstream effect" in (b.last_error or "")
                for b in py_blocks
            ),
            loops_used=final.loop_count,
            output_compared=len(matches),
            output_same=sum(matches.values()),
        ),
        blocks=[
            BlockView(
                id=b.id,
                lang=b.lang,
                status=b.status,
                last_error=_DOWNSTREAM.sub("", b.last_error) if b.last_error else None,
                original_source=b.original_source,
                source=b.source,
                changed=b.source != b.original_source,
                output_matches=matches.get(b.id),
                downstream_of=(
                    m.group(1) if b.last_error and (m := _DOWNSTREAM.search(b.last_error)) else None
                ),
                diagnosis=(
                    d.text
                    if b.status in ("failing", "unresolved")
                    and (d := diagnose(b.last_error, b.source))
                    else None
                ),
            )
            for b in final.blocks
        ],
        changes=[
            ChangeView(
                block_id=e.block_id,
                kind=e.kind,
                symbol=e.symbol,
                description=e.description,
                evidence_chunk_ids=e.evidence_chunk_ids,
                source_urls=e.source_urls,
                cited=e.kind not in ("llm_prior", "defect_fix"),
                error=e.error,
            )
            for e in final.change_log
        ],
        prose_edits=[
            ProseView(old=e.old, new=e.new, reason=e.reason, linked=e.linked)
            for e in final.prose_edits
        ],
        suggestions=dict(final.suggestions),
        warnings=final.warnings,
        migrated={
            lib: {"from": final.old_versions.get(lib, "unknown"), "to": ver}
            for lib, ver in sorted(final.target_versions.items())
        },
    )


STAGES = [
    ("ingest", "Reading the tutorial"),
    ("prerepair", "Adding forgotten imports"),
    ("extract", "Finding the libraries and calls it uses"),
    ("resolve_versions", "Working out the old and new versions"),
    ("precheck", "Running the original to see what already works"),
    ("retrieve", "Looking up what changed in the release notes"),
    ("diff", "Judging which calls changed"),
    ("rewrite", "Rewriting the code"),
    ("sandbox", "Running every block in the sandbox"),
    ("query_rewrite", "Reading errors and retrying"),
    ("repair", "Fixing defects that are not version changes"),
    ("dewarn", "Removing deprecation warnings"),
    ("prose", "Updating the explanation text"),
    ("assemble", "Writing the result"),
]
MODE_LABELS = {
    "protect": "Leave blocks that already run alone",
    "repair": "Fix missing imports and packages",
    "prose": "Update the explanation text",
    "warnings": "Fix deprecation warnings",
}


# the usual import aliases, for pages whose imports were left out of the snippets
ALIASES = {"pandas": ("pd",), "numpy": ("np",), "matplotlib": ("plt",)}


def describe(markdown: str, title: str) -> Preview:
    """What a run would involve, for the page to show before anything is spent."""
    from trf.config import LIBRARIES

    fenced = re.findall(r"^```(?:python|py|pycon)\b.*?^```", markdown, re.S | re.M)
    blocks = len(fenced)
    code = chr(10).join(fenced)
    libs = [
        name
        for name in LIBRARIES
        if re.search(rf"^\s*(?:import|from)\s+{re.escape(name)}\b", code, re.M)
        or any(re.search(rf"\b{a}\.", code) for a in ALIASES.get(name, ()))
    ]
    return Preview(
        title=title,
        markdown=markdown,
        python_blocks=blocks,
        libraries=libs,
        est_seconds=int(30 + blocks * SECONDS_PER_BLOCK),
        est_budget_usd=round(blocks * LEDGER_USD_PER_BLOCK, 3),
    )


def spend_status() -> dict:
    """The model budget so far, in the units the cap is set in (see docs/spend-cap.md)."""
    ledger = Path(os.environ.get("TRF_SPEND_LEDGER", ".trf_cache/spend.json"))
    used = 0.0
    try:
        used = float(json.loads(ledger.read_text(encoding="utf-8"))["usd"])
    except (OSError, ValueError, KeyError):
        pass
    cap = os.environ.get("TRF_MAX_SPEND_USD")
    return {"used": round(used, 3), "cap": float(cap) if cap else None}


def _default_runner(
    state: GraphState,
    on_stage: Callable[[str], None] | None = None,
    modes: dict[str, bool] | None = None,
) -> GraphState:
    from trf.config import PipelineFlags
    from trf.graph import default_nodes, run_graph  # lazy: models/env only needed when a job runs

    flags = PipelineFlags.from_env()
    # real tutorials: leave working code alone, fix forgotten imports, keep the prose in step
    for key, (field, env) in MODES.items():
        if modes and key in modes:  # the person's choice on the page wins
            flags = flags.model_copy(update={field: modes[key]})
        elif env not in os.environ:
            flags = flags.model_copy(update={field: True})
    nodes = default_nodes(flags)
    if on_stage:

        def announce(name, fn):
            def run(s):
                on_stage(name)
                return fn(s)

            return run

        nodes = {name: announce(name, fn) for name, fn in nodes.items()}
    return run_graph(state, flags=flags, nodes=nodes)


def create_app(
    runner: Runner | None = None,
    max_workers: int | None = None,
    jobs_dir: str | Path | None = None,
) -> FastAPI:
    """`jobs_dir` makes jobs survive a restart; `max_workers` defaults to $TRF_API_WORKERS or 1."""
    run = runner or _default_runner
    params = inspect.signature(run).parameters
    wants_stage = "on_stage" in params
    wants_modes = "modes" in params
    workers = max_workers or int(os.environ.get("TRF_API_WORKERS", "1"))
    jobs = _Jobs(jobs_dir)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="trf-job")
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app = FastAPI(title="Tutorial Rot Fixer")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def work(job_id: str, original_md: str, state: GraphState, modes: dict[str, bool]) -> None:
        jobs.set_status(job_id, "running")
        try:
            extra: dict = {}
            if wants_stage:
                extra["on_stage"] = lambda s: jobs.set_stage(job_id, s)
            if wants_modes:
                extra["modes"] = modes
            final = run(state, **extra)
            result = build_result(original_md, final)
        except Exception as e:  # a failing job must never take the server down
            jobs.set_status(job_id, "failed", error=f"{type(e).__name__}: {e}"[:ERROR_CHARS])
        else:
            jobs.set_status(job_id, "done", result=result)

    def find(job_id: str) -> JobView:
        view = jobs.get(job_id)
        if view is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return view

    def source_of(body: JobRequest) -> tuple[str, str]:
        """(markdown, title) from pasted text or from a fetched page."""
        if body.url and body.url.strip():
            try:
                return fetch_markdown(body.url)
            except FetchError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        md = body.markdown or ""
        heading = next((ln.lstrip("# ").strip() for ln in md.splitlines() if ln[:1] == "#"), "")
        return md, heading or "Pasted tutorial"

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/api/config")
    def config() -> dict:
        return {
            "stages": STAGES,
            "modes": {k: v for k, v in MODE_LABELS.items()},
            "max_markdown_chars": MAX_MARKDOWN_CHARS,
        }

    @app.get("/api/spend")
    def spend() -> dict:
        return spend_status()

    @app.post("/api/preview")
    def preview(body: JobRequest) -> Preview:
        md, title = source_of(body)
        return describe(md, title)

    @app.get("/api/jobs")
    def list_jobs() -> list[JobSummary]:
        return jobs.summaries()

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response((STATIC_DIR / "favicon.ico").read_bytes(), media_type="image/x-icon")

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/jobs", status_code=202)
    def create_job(body: JobRequest) -> JobCreated:
        md, title = source_of(body)
        if len(md) > MAX_MARKDOWN_CHARS:
            raise HTTPException(status_code=422, detail="That tutorial is too long.")
        job_id = jobs.create(title)
        state = GraphState(
            tutorial_md=md,
            max_loops=body.max_loops,
            user_target_pins=body.target_pins,
        )
        pool.submit(work, job_id, md, state, body.modes)
        return JobCreated(id=job_id)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> JobView:
        return find(job_id)

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str) -> Response:
        view = find(job_id)
        if view.status != "done" or view.result is None:
            raise HTTPException(status_code=409, detail="job is not done")
        return Response(
            view.result.output_md,
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="tutorial-fixed.md"'},
        )

    return app


app = create_app(jobs_dir=os.environ.get("TRF_JOBS_DIR", ".trf_cache/jobs"))

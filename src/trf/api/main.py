"""FastAPI service: submit a tutorial, poll the job, read the per-block diff, download the fix."""

from __future__ import annotations

import inspect
import json
import os
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from trf.nodes.diagnose import diagnose
from trf.state import BlockStatus, ChangeKind, GraphState

STATIC_DIR = Path(__file__).parent / "static"
MAX_MARKDOWN_CHARS = 200_000
MAX_JOBS = 100
ERROR_CHARS = 500

Runner = Callable[..., GraphState]  # (state) or (state, on_stage=callable)
JobStatus = Literal["queued", "running", "done", "failed"]


class JobRequest(BaseModel):
    markdown: str = Field(max_length=MAX_MARKDOWN_CHARS)
    max_loops: int = Field(default=4, ge=0, le=8)
    target_pins: dict[str, str] = Field(default_factory=dict)

    @field_validator("markdown")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("markdown must not be empty")
        return v


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
    output_compared: int = 0
    output_same: int = 0


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
    stage: str | None = None  # pipeline stage while running
    error: str | None = None
    result: JobResult | None = None


@dataclass
class _Job:
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
            "status": j.status,
            "stage": j.stage,
            "error": j.error,
            "result": j.result.model_dump() if j.result else None,
        }
        tmp = self._dir / f"{job_id}.tmp"
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self._dir / f"{job_id}.json")

    def create(self) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = _Job()
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
                status=job.status,
                stage=job.stage,
                error=job.error,
                result=job.result,
            )


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
            loops_used=final.loop_count,
            output_compared=len(matches),
            output_same=sum(matches.values()),
        ),
        blocks=[
            BlockView(
                id=b.id,
                lang=b.lang,
                status=b.status,
                last_error=b.last_error,
                original_source=b.original_source,
                source=b.source,
                changed=b.source != b.original_source,
                output_matches=matches.get(b.id),
                diagnosis=(
                    d.text
                    if b.status in ("failing", "unresolved") and (d := diagnose(b.last_error))
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


def _default_runner(state: GraphState, on_stage: Callable[[str], None] | None = None) -> GraphState:
    from trf.config import PipelineFlags
    from trf.graph import default_nodes, run_graph  # lazy: models/env only needed when a job runs

    flags = PipelineFlags.from_env()
    # real tutorials: leave working code alone, fix forgotten imports, keep the prose in step
    for env, field in (
        ("TRF_PROTECT_WORKING_BLOCKS", "protect_working_blocks"),
        ("TRF_REPAIR_DEFECTS", "repair_defects"),
        ("TRF_UPDATE_PROSE", "update_prose"),
        ("TRF_FIX_WARNINGS", "fix_warnings"),
    ):
        if env not in os.environ:
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
    wants_stage = "on_stage" in inspect.signature(run).parameters
    workers = max_workers or int(os.environ.get("TRF_API_WORKERS", "1"))
    jobs = _Jobs(jobs_dir)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="trf-job")
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app = FastAPI(title="Tutorial Rot Fixer")

    def work(job_id: str, original_md: str, state: GraphState) -> None:
        jobs.set_status(job_id, "running")
        try:
            final = (
                run(state, on_stage=lambda s: jobs.set_stage(job_id, s))
                if wants_stage
                else run(state)
            )
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

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/jobs", status_code=202)
    def create_job(body: JobRequest) -> JobCreated:
        job_id = jobs.create()
        state = GraphState(
            tutorial_md=body.markdown,
            max_loops=body.max_loops,
            user_target_pins=body.target_pins,
        )
        pool.submit(work, job_id, body.markdown, state)
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

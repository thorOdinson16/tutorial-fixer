"""FastAPI service: submit a tutorial, poll the job, read the per-block diff, download the fix."""

from __future__ import annotations

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

from trf.state import BlockStatus, ChangeKind, GraphState

STATIC_DIR = Path(__file__).parent / "static"
MAX_MARKDOWN_CHARS = 200_000
MAX_JOBS = 100
ERROR_CHARS = 500

Runner = Callable[[GraphState], GraphState]
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


class ChangeView(BaseModel):
    block_id: str
    kind: ChangeKind
    symbol: str | None
    description: str
    evidence_chunk_ids: list[str]
    source_urls: list[str]
    cited: bool


class Summary(BaseModel):
    total_python_blocks: int
    passing: int
    unresolved: int
    loops_used: int


class JobResult(BaseModel):
    original_md: str
    output_md: str
    change_log_md: str
    summary: Summary
    blocks: list[BlockView]
    changes: list[ChangeView]
    warnings: list[str]
    migrated: dict[str, dict[str, str]]


class JobView(BaseModel):
    id: str
    status: JobStatus
    error: str | None = None
    result: JobResult | None = None


@dataclass
class _Job:
    status: JobStatus = "queued"
    error: str | None = None
    result: JobResult | None = None


class _Jobs:
    """Thread-safe in-memory job table. Past MAX_JOBS, the oldest finished jobs are dropped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}

    def create(self) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = _Job()
            for old_id in list(self._jobs):
                if len(self._jobs) <= MAX_JOBS:
                    break
                if self._jobs[old_id].status in ("done", "failed"):
                    del self._jobs[old_id]
        return job_id

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

    def get(self, job_id: str) -> JobView | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return JobView(id=job_id, status=job.status, error=job.error, result=job.result)


def build_result(original_md: str, final: GraphState) -> JobResult:
    """Convert the finished pipeline state into the API payload. Only status 'passing' counts."""
    py_blocks = [b for b in final.blocks if b.lang == "python"]
    return JobResult(
        original_md=original_md,
        output_md=final.output_md,
        change_log_md=final.change_log_md,
        summary=Summary(
            total_python_blocks=len(py_blocks),
            passing=sum(b.status == "passing" for b in py_blocks),
            unresolved=sum(b.status == "unresolved" for b in py_blocks),
            loops_used=final.loop_count,
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
                cited=e.kind != "llm_prior",
            )
            for e in final.change_log
        ],
        warnings=final.warnings,
        migrated={
            lib: {"from": final.old_versions.get(lib, "unknown"), "to": ver}
            for lib, ver in sorted(final.target_versions.items())
        },
    )


def _default_runner(state: GraphState) -> GraphState:
    from trf.config import PipelineFlags
    from trf.graph import run_graph  # lazy: models and env vars are needed only when a job runs

    return run_graph(state, flags=PipelineFlags.from_env())


def create_app(runner: Runner | None = None, max_workers: int = 1) -> FastAPI:
    run = runner or _default_runner
    jobs = _Jobs()
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="trf-job")
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app = FastAPI(title="Tutorial Rot Fixer")

    def work(job_id: str, original_md: str, state: GraphState) -> None:
        jobs.set_status(job_id, "running")
        try:
            result = build_result(original_md, run(state))
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


app = create_app()

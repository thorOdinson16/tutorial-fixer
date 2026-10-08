from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from trf.api.main import STATIC_DIR, build_result, create_app
from trf.state import ChangeLogEntry, CodeBlock, GraphState

MD = "# Pandas\n\n```python\nx = 1\n```\n\n```python\ny()\n```\n"


def fake_runner(state: GraphState) -> GraphState:
    ok = CodeBlock(id="b1", source="x = 2\n", original_source="x = 1\n", status="passing")
    bad = CodeBlock(
        id="b2",
        source="y()\n",
        original_source="y()\n",
        status="unresolved",
        last_error="NameError",
    )
    return GraphState(
        tutorial_md=state.tutorial_md,
        blocks=[ok, bad],
        change_log=[
            ChangeLogEntry(
                block_id="b1",
                kind="api_change",
                symbol="x",
                description="x is now 2",
                evidence_chunk_ids=["c1"],
                source_urls=["https://example.com/x"],
            )
        ],
        output_md="# fixed\n",
        change_log_md="# log\n",
        old_versions={"pandas": "1.5.0"},
        target_versions={"pandas": "2.2.0"},
        loop_count=2,
        warnings=["careful"],
        unresolved_block_ids=["b2"],
    )


def wait_for(client: TestClient, job_id: str, statuses: tuple[str, ...]) -> dict:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job did not reach {statuses} in time")


def test_health() -> None:
    with TestClient(create_app(runner=fake_runner)) as client:
        assert client.get("/health").json() == {"ok": True}


def test_index_serves_page_without_innerhtml() -> None:
    with TestClient(create_app(runner=fake_runner)) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Tutorial Rot Fixer" in resp.text
    assert "innerHTML" not in resp.text


def test_job_completes_with_result() -> None:
    with TestClient(create_app(runner=fake_runner)) as client:
        resp = client.post("/api/jobs", json={"markdown": MD, "max_loops": 2})
        assert resp.status_code == 202
        job = resp.json()
        assert job["status"] == "queued"
        body = wait_for(client, job["id"], ("done", "failed"))
        assert body["status"] == "done", body
        result = body["result"]
        assert result["original_md"] == MD
        assert result["output_md"] == "# fixed\n"
        assert result["change_log_md"] == "# log\n"
        assert result["summary"] == {
            "total_python_blocks": 2,
            "passing": 1,
            "unresolved": 1,
            "loops_used": 2,
        }
        blocks = {b["id"]: b for b in result["blocks"]}
        assert blocks["b1"]["status"] == "passing"
        assert blocks["b1"]["changed"] is True
        assert blocks["b2"]["status"] == "unresolved"
        assert blocks["b2"]["last_error"] == "NameError"
        assert blocks["b2"]["changed"] is False
        assert len(result["changes"]) == 1
        change = result["changes"][0]
        assert change["cited"] is True
        assert change["evidence_chunk_ids"] == ["c1"]
        assert change["source_urls"] == ["https://example.com/x"]
        assert result["migrated"] == {"pandas": {"from": "1.5.0", "to": "2.2.0"}}
        assert result["warnings"] == ["careful"]


def test_invalid_requests_return_422() -> None:
    with TestClient(create_app(runner=fake_runner)) as client:
        assert client.post("/api/jobs", json={"markdown": "   "}).status_code == 422
        assert client.post("/api/jobs", json={"markdown": ""}).status_code == 422
        bad_loops = {"markdown": MD, "max_loops": 99}
        assert client.post("/api/jobs", json=bad_loops).status_code == 422


def test_unknown_job_is_404() -> None:
    with TestClient(create_app(runner=fake_runner)) as client:
        assert client.get("/api/jobs/deadbeef").status_code == 404
        assert client.get("/api/jobs/deadbeef/download").status_code == 404


def test_failed_runner_reports_error_and_server_keeps_working() -> None:
    def boom(state: GraphState) -> GraphState:
        raise RuntimeError("kaboom")

    with TestClient(create_app(runner=boom)) as client:
        job = client.post("/api/jobs", json={"markdown": MD}).json()
        body = wait_for(client, job["id"], ("done", "failed"))
        assert body["status"] == "failed"
        assert body["error"] == "RuntimeError: kaboom"
        assert body["result"] is None
        assert client.get("/health").json() == {"ok": True}


def test_download_is_409_until_done() -> None:
    release = threading.Event()

    def slow(state: GraphState) -> GraphState:
        release.wait(5)
        return fake_runner(state)

    with TestClient(create_app(runner=slow)) as client:
        try:
            job = client.post("/api/jobs", json={"markdown": MD}).json()
            assert client.get(f"/api/jobs/{job['id']}").json()["status"] in ("queued", "running")
            assert client.get(f"/api/jobs/{job['id']}/download").status_code == 409
        finally:
            release.set()
        wait_for(client, job["id"], ("done", "failed"))
        resp = client.get(f"/api/jobs/{job['id']}/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.text == "# fixed\n"


def test_build_result_marks_llm_prior_as_uncited() -> None:
    state = GraphState(
        tutorial_md=MD,
        blocks=[CodeBlock(id="b1", source="x = 2\n", original_source="x = 1\n", status="failing")],
        change_log=[ChangeLogEntry(block_id="b1", kind="llm_prior", description="guess")],
    )
    result = build_result(MD, state)
    assert result.changes[0].cited is False
    assert result.summary.passing == 0
    assert result.summary.unresolved == 0
    assert result.blocks[0].changed is True


def test_stacked_layout_keeps_code_panes_visible():
    """Regression: with `flex: 1 1 0` in a column, the panes collapsed to zero height under 800px."""
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "@media (max-width: 800px)" in page
    assert ".col { flex: 0 0 auto; }" in page

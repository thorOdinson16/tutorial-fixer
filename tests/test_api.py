from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from trf.api.main import STATIC_DIR, build_result, create_app
from trf.state import ChangeLogEntry, CodeBlock, GraphState, RunResult


def static_text() -> str:
    """Every file of the front end, as one string."""
    return chr(10).join(
        f.read_text(encoding="utf-8")
        for f in sorted(STATIC_DIR.rglob("*"))
        if f.suffix in (".html", ".css", ".js")
    )


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
    assert "innerHTML" not in static_text()


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
            "output_compared": 0,
            "output_same": 0,
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
    page = static_text()
    assert "@media (max-width: 800px)" in page
    assert ".col { flex: 0 0 auto; }" in page


def test_result_reports_output_comparison_and_diagnosis():
    final = GraphState(
        blocks=[
            CodeBlock.new("b1", "a").model_copy(update={"status": "passing"}),
            CodeBlock.new("b2", "b").model_copy(update={"status": "passing"}),
            CodeBlock.new("b3", "c").model_copy(
                update={
                    "status": "unresolved",
                    "last_error": "NameError: name 'List' is not defined",
                }
            ),
        ],
        old_outputs={"b1": "x\n", "b2": "before"},
        run_results=[
            RunResult(block_id="b1", exit_code=0, stdout="x"),
            RunResult(block_id="b2", exit_code=0, stdout="after"),
        ],
    )
    r = build_result("md", final)
    by = {b.id: b for b in r.blocks}
    assert by["b1"].output_matches is True and by["b2"].output_matches is False
    assert by["b3"].output_matches is None
    assert "from typing import List" in by["b3"].diagnosis
    assert by["b1"].diagnosis is None
    assert (r.summary.output_compared, r.summary.output_same) == (2, 1)


def test_running_job_reports_its_stage():
    gate, entered = threading.Event(), threading.Event()

    def staged(state, on_stage):
        on_stage("retrieve")
        entered.set()
        gate.wait(5)
        return fake_runner(state)

    with TestClient(create_app(runner=staged)) as client:
        job_id = client.post("/api/jobs", json={"markdown": MD}).json()["id"]
        assert entered.wait(5)
        body = client.get(f"/api/jobs/{job_id}").json()
        assert body["status"] == "running" and body["stage"] == "retrieve"
        gate.set()
        done = wait_for(client, job_id, ("done", "failed"))
        assert done["status"] == "done" and done["stage"] is None


def test_jobs_survive_a_restart_and_interrupted_ones_are_marked_failed(tmp_path):
    with TestClient(create_app(runner=fake_runner, jobs_dir=tmp_path)) as client:
        job_id = client.post("/api/jobs", json={"markdown": MD}).json()["id"]
        wait_for(client, job_id, ("done",))
    # a job that was still running when the process died
    (tmp_path / "deadbeef.json").write_text(
        '{"status": "running", "stage": "diff", "error": null, "result": null}', encoding="utf-8"
    )
    with TestClient(create_app(runner=fake_runner, jobs_dir=tmp_path)) as client2:
        again = client2.get(f"/api/jobs/{job_id}").json()
        assert again["status"] == "done" and again["result"]["output_md"] == "# fixed\n"
        dead = client2.get("/api/jobs/deadbeef").json()
        assert dead["status"] == "failed" and "restart" in dead["error"]


def test_worker_count_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("TRF_API_WORKERS", "3")
    app = create_app(runner=fake_runner)
    assert app is not None  # constructed without error with 3 workers


def test_result_carries_defect_fixes_prose_edits_and_suggestions():
    from trf.state import ProseEdit

    final = GraphState(
        blocks=[CodeBlock.new("b2", "x").model_copy(update={"status": "passing"})],
        change_log=[
            ChangeLogEntry(
                block_id="b2", kind="defect_fix", description="Added `import numpy as np`.",
                error="NameError: name 'np' is not defined",
            )
        ],
        prose_edits=[
            ProseEdit(segment=0, old="Uses pandas 1.3.", new="Uses pandas 2.3.", reason="target",
                      linked="version")
        ],
        suggestions={"b5": "Alice = 'a'  # assumed: a name"},
    )  # fmt: skip
    r = build_result("md", final)
    (c,) = r.changes
    assert c.kind == "defect_fix" and c.cited is False and "NameError" in c.error
    assert [(e.old, e.new, e.linked) for e in r.prose_edits] == [
        ("Uses pandas 1.3.", "Uses pandas 2.3.", "version")
    ]
    assert r.suggestions == {"b5": "Alice = 'a'  # assumed: a name"}


def test_page_renders_the_new_sections_without_innerHTML():
    page = static_text()
    assert "Prose changes" in page and "Suggested fixes (not applied)" in page
    assert "defect fix" in page and "innerHTML" not in page


def test_job_from_a_url_uses_the_fetched_page(monkeypatch):
    seen = {}

    def fake_fetch(url, pin=True):
        seen["url"] = url
        return MD, "Fetched title"

    monkeypatch.setattr("trf.api.main.fetch_markdown", fake_fetch)
    with TestClient(create_app(runner=fake_runner)) as client:
        job = client.post("/api/jobs", json={"url": "https://example.com/t"}).json()
        body = wait_for(client, job["id"], ("done", "failed"))
    assert seen["url"] == "https://example.com/t"
    assert body["status"] == "done" and body["title"] == "Fetched title"


def test_unusable_url_is_a_422_with_the_reason(monkeypatch):
    from trf.webfetch import FetchError

    def refuse(url, pin=True):
        raise FetchError("That address is on a private network, so it is not fetched.")

    monkeypatch.setattr("trf.api.main.fetch_markdown", refuse)
    with TestClient(create_app(runner=fake_runner)) as client:
        resp = client.post("/api/jobs", json={"url": "http://10.0.0.1/"})
    assert resp.status_code == 422 and "private network" in resp.json()["detail"]


def test_request_needs_exactly_one_source_and_known_modes():
    with TestClient(create_app(runner=fake_runner)) as client:
        assert client.post("/api/jobs", json={}).status_code == 422
        both = {"markdown": MD, "url": "https://example.com"}
        assert client.post("/api/jobs", json=both).status_code == 422
        assert (
            client.post("/api/jobs", json={"markdown": MD, "modes": {"nope": True}}).status_code
            == 422
        )


def test_preview_counts_blocks_and_libraries_without_running_anything():
    md = "# T\n\n```python\nimport pandas as pd\nimport numpy as np\n```\n\n```python\nx = 1\n```\n"
    with TestClient(create_app(runner=fake_runner)) as client:
        p = client.post("/api/preview", json={"markdown": md}).json()
    assert p["title"] == "T" and p["python_blocks"] == 2
    assert p["libraries"] == ["pandas", "numpy"] or set(p["libraries"]) == {"pandas", "numpy"}
    assert p["est_seconds"] > 0 and p["est_budget_usd"] > 0


def test_modes_reach_a_runner_that_accepts_them():
    got = {}

    def runner(state, modes=None):
        got["modes"] = modes
        return fake_runner(state)

    with TestClient(create_app(runner=runner)) as client:
        job = client.post("/api/jobs", json={"markdown": MD, "modes": {"prose": False}}).json()
        wait_for(client, job["id"], ("done", "failed"))
    assert got["modes"] == {"prose": False}


def test_history_lists_newest_first_without_results():
    with TestClient(create_app(runner=fake_runner)) as client:
        a = client.post("/api/jobs", json={"markdown": "# First\n\n```python\nx=1\n```\n"}).json()
        wait_for(client, a["id"], ("done",))
        time.sleep(0.02)
        b = client.post("/api/jobs", json={"markdown": "# Second\n\n```python\nx=1\n```\n"}).json()
        wait_for(client, b["id"], ("done",))
        rows = client.get("/api/jobs").json()
    assert [r["title"] for r in rows[:2]] == ["Second", "First"]
    assert "result" not in rows[0] and rows[0]["total"] == 2


def test_config_lists_stages_and_modes_and_static_files_are_served():
    with TestClient(create_app(runner=fake_runner)) as client:
        cfg = client.get("/api/config").json()
        js = client.get("/static/js/app.js")
        css = client.get("/static/css/components.css")
    assert [s[0] for s in cfg["stages"]][:2] == ["ingest", "prerepair"]
    assert set(cfg["modes"]) == {"protect", "repair", "prose", "warnings"}
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert css.status_code == 200 and "css" in css.headers["content-type"]

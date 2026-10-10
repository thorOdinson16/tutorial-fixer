"""Docker smoke tests. Skipped (not faked) when no Docker daemon is reachable."""

import os
import subprocess
from pathlib import Path

import pytest

from trf.config import SandboxSettings
from trf.nodes.sandbox import docker_available, run_offline
from trf.sandbox_runner.runner import parse_traceback

RUNNER_DIR = Path(__file__).parents[1] / "src" / "trf" / "sandbox_runner"
IMAGE = "trf-sandbox:test"

needs_docker = pytest.mark.skipif(
    bool(os.environ.get("TRF_SKIP_DOCKER_TESTS")) or not docker_available(),
    reason="Docker daemon not available (or TRF_SKIP_DOCKER_TESTS is set)",
)


def test_parse_traceback():
    err = "noise\nTraceback (most recent call last):\n  File x\nValueError: boom\n"
    assert parse_traceback(err).endswith("ValueError: boom")
    assert parse_traceback("nothing") is None


@pytest.fixture(scope="module")
def settings():
    subprocess.run(
        ["docker", "build", "-t", IMAGE, str(RUNNER_DIR)], check=True, capture_output=True
    )
    return SandboxSettings(image=IMAGE, timeout_s=5)


@pytest.mark.docker
@needs_docker
def test_print_ok_offline(settings):
    r = run_offline('print("ok")', settings)
    assert r["exit_code"] == 0 and r["stdout"].strip() == "ok"


@pytest.mark.docker
@needs_docker
def test_network_is_disabled(settings):
    code = "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=3)"
    r = run_offline(code, settings)
    assert r["exit_code"] != 0 and r["traceback"]


@pytest.mark.docker
@needs_docker
def test_timeout_enforced(settings):
    r = run_offline("while True: pass", settings)
    assert r["timed_out"] is True


@pytest.mark.docker
@needs_docker
def test_exception_captured(settings):
    r = run_offline("raise ValueError('boom')", settings)
    assert r["exit_code"] == 1 and "ValueError: boom" in r["traceback"]


# --- the real driver, inside a real container (stdlib only: no dependency install) -----------


def _run_node(settings, blocks, assertions=None):
    from trf.nodes.sandbox import make_sandbox
    from trf.state import GraphState

    node = make_sandbox(env_builder=lambda r, o, s: IMAGE, settings=settings)
    return GraphState.model_validate(
        GraphState(blocks=blocks, assertions=assertions or {}).model_dump()
        | node(GraphState(blocks=blocks, assertions=assertions or {}))
    )


@pytest.mark.docker
@needs_docker
def test_node_shared_namespace_and_attribution(settings):
    from trf.state import Assertion, CodeBlock

    blocks = [
        CodeBlock.new("b1", "x = 40\nprint('first')\n"),
        CodeBlock.new("b2", "print(x + 2)\n"),
        CodeBlock.new("b3", "raise KeyError('nope')\n"),
        CodeBlock.new("b4", "def broken(:\n"),
        CodeBlock.new("b5", "print('still runs')\n"),
        CodeBlock.new("b6", "print('wrong')\n"),
    ]
    out = _run_node(
        settings,
        blocks,
        {"b2": Assertion(stdout_contains=["42"], snippet="assert x == 40"),
         "b6": Assertion(stdout_contains=["right"])},
    )  # fmt: skip
    st = {b.id: b.status for b in out.blocks}
    assert st == {"b1": "passing", "b2": "passing", "b3": "failing", "b4": "failing",
                  "b5": "passing", "b6": "failing"}  # fmt: skip
    by = {b.id: b for b in out.blocks}
    assert by["b3"].last_error == "KeyError: 'nope'"
    assert "SyntaxError" in by["b4"].last_error and "earlier failure in b3" in by["b4"].last_error
    assert "expected text" in by["b6"].last_error
    res = {r.block_id: r for r in out.run_results}
    assert res["b2"].passed_assertions is True and res["b2"].stdout == "42"
    assert res["b3"].traceback and "<block:b3>" in res["b3"].traceback


@pytest.mark.docker
@needs_docker
def test_node_timeout_is_failure_not_pass(settings):
    from trf.state import CodeBlock

    srcs = ["print('a')\n", "while True: pass\n", "print('c')\n"]
    out = _run_node(settings, [CodeBlock.new(f"b{i}", s) for i, s in enumerate(srcs, 1)])
    st = {b.id: b.status for b in out.blocks}
    assert st == {"b1": "passing", "b2": "failing", "b3": "pending"}
    assert "timed out" in out.blocks[1].last_error

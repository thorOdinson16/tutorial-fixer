"""Sandbox logic without Docker: driver construction, output attribution, node wiring."""

import ast

import pytest

from trf.config import SandboxSettings
from trf.nodes.sandbox import (
    EnvBuildError,
    build_driver,
    check_requirements,
    env_tag,
    interpret,
    make_sandbox,
    split_by_unit,
)
from trf.state import Assertion, CodeBlock, GraphState


def B(id, src="pass\n", lang="python"):
    return CodeBlock.new(id, src, lang)


def raw(stdout="", stderr="", timed_out=False):
    return {"stdout": stdout, "stderr": stderr, "timed_out": timed_out, "exit_code": 0}


def begin(*ids, text=None):
    return "".join(f"<<<TRF:BEGIN:{i}>>>\n{(text or {}).get(i, '')}" for i in ids)


def test_driver_is_valid_python_and_embeds_units():
    d = build_driver(
        [B("b1", "x = 1\n"), B("b2", "!pip install q\ny = 2\n")],
        {"b1": Assertion(snippet="assert x == 1")},
    )
    tree = ast.parse(d)  # parse only; never executed on the host
    assert tree and "b1:assert" in d and "pass\\ny = 2" in d  # magics neutralised


def test_split_by_unit():
    t = "<<<TRF:BEGIN:b1>>>\nhello\n<<<TRF:BEGIN:b2>>>\nworld\n"
    assert split_by_unit(t) == {"b1": "hello", "b2": "world"}


def test_interpret_pass_and_fail_with_cascade_hint():
    stdout = begin("b1", "b2", "b3", text={"b1": "hi\n", "b2": "", "b3": "x\n"})
    tb = 'Traceback (most recent call last):\n  File "<block:b2>", line 1\nAttributeError: boom'
    stderr = begin(
        "b1",
        "b2",
        "b3",
        text={"b2": tb + "\n<<<TRF:FAIL>>>\n", "b3": "NameError: n\n<<<TRF:FAIL>>>\n"},
    )
    results, upd = interpret([B("b1"), B("b2"), B("b3")], {}, raw(stdout, stderr))
    assert upd["b1"] == ("passing", None)
    assert upd["b2"] == ("failing", "AttributeError: boom")
    assert upd["b3"][0] == "failing" and "after earlier failure in b2" in upd["b3"][1]
    assert results[1].traceback.startswith("Traceback") and results[1].exit_code == 1
    assert results[0].stdout == "hi" and results[0].passed_assertions is None


def test_interpret_stdout_assertion():
    a = {"b1": Assertion(stdout_contains=["42"])}
    ok, _ = interpret([B("b1")], a, raw(begin("b1", text={"b1": "answer 42\n"}), begin("b1")))
    assert ok[0].passed_assertions is True and ok[0].ok
    bad, upd = interpret([B("b1")], a, raw(begin("b1", text={"b1": "answer 41\n"}), begin("b1")))
    assert bad[0].passed_assertions is False and not bad[0].ok
    assert upd["b1"][0] == "failing" and "42" in upd["b1"][1]


def test_interpret_snippet_assertion_failure():
    a = {"b1": Assertion(snippet="assert x == 2")}
    stderr = begin("b1", "b1:assert", text={"b1:assert": "AssertionError\n<<<TRF:FAIL>>>\n"})
    res, upd = interpret([B("b1")], a, raw(begin("b1", "b1:assert"), stderr))
    assert upd["b1"] == ("failing", "assertion failed: AssertionError")
    assert res[0].passed_assertions is False


def test_interpret_timeout_marks_in_flight_block_and_skips_rest():
    res, upd = interpret(
        [B("b1"), B("b2"), B("b3")], {}, raw(begin("b1", "b2"), begin("b1", "b2"), timed_out=True)
    )
    assert upd["b1"][0] == "passing"
    assert upd["b2"][0] == "failing" and "timed out" in upd["b2"][1] and res[1].timed_out
    assert "b3" not in upd  # never ran: status untouched, nothing claimed


def _node(executor, builder=lambda r, o, s: "img"):
    return make_sandbox(env_builder=builder, executor=executor, settings=SandboxSettings())


def test_node_updates_blocks_and_counts_loop():
    def executor(code, settings):
        assert settings.image == "img"
        return raw(begin("b2"), begin("b2", text={"b2": "ValueError: x\n<<<TRF:FAIL>>>\n"}))

    s = GraphState(blocks=[B("b1", "echo", "bash"), B("b2")], loop_count=1)
    out = _node(executor)(s)
    assert out["loop_count"] == 2
    assert [b.status for b in out["blocks"]] == ["pending", "failing"]  # bash block untouched
    assert out["run_results"][0].block_id == "b2"


def test_node_reports_build_failure_not_success():
    def builder(r, o, s):
        raise EnvBuildError("no matching distribution")

    out = _node(lambda c, s: pytest.fail("must not execute"), builder)(GraphState(blocks=[B("b1")]))
    assert (
        out["blocks"][0].status == "failing"
        and "environment build failed" in out["blocks"][0].last_error
    )


def test_node_handles_no_output():
    out = _node(lambda c, s: raw(timed_out=True))(GraphState(blocks=[B("b1")]))
    assert out["blocks"][0].status == "failing" and "timed out" in out["blocks"][0].last_error


def test_node_no_python_blocks_is_noop():
    out = _node(lambda c, s: pytest.fail("no"))(GraphState(blocks=[B("b1", "x", "bash")]))
    assert out == {"loop_count": 1}


def test_requirement_validation_and_tag_stability():
    assert check_requirements(["pandas==2.2.3", "numpy<2", "scikit-learn"])
    with pytest.raises(EnvBuildError):
        check_requirements(["pandas; rm -rf /"])
    assert env_tag("3.12", ["a==1", "b==2"], []) == env_tag("3.12", ["b==2", "a==1"], [])
    assert env_tag("3.12", ["a==1"], []) != env_tag("3.10", ["a==1"], [])


def test_last_line_skips_pydantic_docs_url():
    from trf.nodes.sandbox import _last_line

    tb = (
        "Traceback (most recent call last):\n  File x\n"
        "pydantic.errors.PydanticImportError: `BaseSettings` has been moved\n\n"
        "For further information visit https://errors.pydantic.dev/2.13/u/import-error"
    )
    assert _last_line(tb).startswith("pydantic.errors.PydanticImportError: `BaseSettings`")
    assert _last_line("a\nplain tail") == "plain tail"

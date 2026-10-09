"""Run code blocks in the Docker sandbox. Tutorial code NEVER runs on the host.

Flow per run:
  1. `ensure_env_image`: build (and cache by hash) an image with exactly the target-pinned
     libraries installed. Network is available only during this build.
  2. `run_offline`: one container, `--network none`, read-only FS, mem/cpu/pid/time limits.
  3. All runnable python blocks execute in ONE shared namespace (tutorials are cumulative) via a
     driver that execs each block separately, so every block gets its own stdout/stderr/status
     even when an earlier block failed. Blocks after a failure may fail only as a downstream
     effect; their `last_error` says so.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from trf.config import SandboxSettings, get_library
from trf.nodes.extract import strip_magics
from trf.state import Assertion, CodeBlock, GraphState, RunResult

RUNNER_DIR = Path(__file__).parents[1] / "sandbox_runner"
_REQ_OK = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]]*(?:(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_\-]+,?)*$"
)
MAX_BLOCK_OUT = 20_000


class SandboxUnavailable(RuntimeError):
    pass


class EnvBuildError(RuntimeError):
    pass


def docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# --- environment image --------------------------------------------------------------------


def check_requirements(reqs: list[str]) -> list[str]:
    bad = [r for r in reqs if not _REQ_OK.match(r)]
    if bad:
        raise EnvBuildError(f"refusing suspicious requirement strings: {bad}")
    return reqs


def env_tag(python_version: str, reqs: list[str], optional: list[str]) -> str:
    key = json.dumps([python_version, sorted(reqs), sorted(optional)])
    return f"trf-env:py{python_version}-{hashlib.sha1(key.encode()).hexdigest()[:12]}"


def ensure_env_image(
    reqs: list[str], optional: list[str], settings: SandboxSettings, *, build_timeout_s: int = 900
) -> str:
    """Return the tag of an image with `reqs` (strict) and `optional` (best-effort) installed."""
    check_requirements(reqs)
    check_requirements(optional)
    tag = env_tag(settings.python_version, reqs, optional)
    have = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    if have.returncode == 0:
        return tag
    cmd = [
        "docker", "build", "-t", tag,
        "--build-arg", f"PYTHON_VERSION={settings.python_version}",
        "--build-arg", f"REQUIREMENTS={' '.join(reqs)}",
        "--build-arg", f"OPTIONAL_REQUIREMENTS={' '.join(optional)}",
        str(RUNNER_DIR),
    ]  # fmt: skip
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=build_timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        raise EnvBuildError("environment build timed out") from e
    if p.returncode != 0:
        raise EnvBuildError((p.stdout + p.stderr)[-1500:])
    return tag


def requirements_for(state: GraphState) -> tuple[list[str], list[str]]:
    reqs = [
        f"{get_library(lib).pypi_name}=={ver}" for lib, ver in sorted(state.target_versions.items())
    ]
    return reqs, sorted(state.extra_packages)


# --- execution ----------------------------------------------------------------------------


def run_offline(code: str, settings: SandboxSettings | None = None) -> dict:
    """Run `code` in a fresh container with no network and hard resource limits.

    Returns the runner's JSON: {exit_code, stdout, stderr, traceback, timed_out}.
    """
    s = settings or SandboxSettings()
    cmd = [
        "docker", "run", "--rm", "-i",
        "--network", "none",
        "--memory", s.memory,
        "--memory-swap", s.memory,
        "--cpus", str(s.cpus),
        "--pids-limit", str(s.pids_limit),
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        s.image,
    ]  # fmt: skip
    payload = json.dumps({"code": code, "timeout_s": s.timeout_s})
    try:
        proc = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=s.timeout_s + 30,
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "container timeout",
                "traceback": None, "timed_out": True}  # fmt: skip
    if proc.returncode != 0 and not proc.stdout.strip():
        raise SandboxUnavailable(f"docker run failed ({proc.returncode}): {proc.stderr[:500]}")
    return json.loads(proc.stdout)


_DRIVER = """\
import sys as _trf_sys, traceback as _trf_tb, warnings as _trf_w, json as _trf_json

_trf_units = {units!r}


def _trf_main():
    ok = True
    for uid, src, needs_prev_ok in _trf_units:
        if needs_prev_ok and not ok:
            continue
        for stream in (_trf_sys.stdout, _trf_sys.stderr):
            print("<<<TRF:BEGIN:" + uid + ">>>", file=stream, flush=True)
        _trf_cm = _trf_w.catch_warnings(record=True)
        _trf_caught = _trf_cm.__enter__()
        _trf_w.simplefilter("always")
        try:
            exec(compile(src, "<block:" + uid + ">", "exec"), globals())
            ok = True
        except SystemExit as e:
            ok = e.code in (None, 0)
            if not ok:
                print("SystemExit: " + repr(e.code), file=_trf_sys.stderr)
        except BaseException as e:
            ok = False
            tb = e.__traceback__.tb_next if e.__traceback__ else None
            _trf_tb.print_exception(type(e), e, tb)
        _trf_cm.__exit__(None, None, None)
        _trf_seen = set()
        for _w in _trf_caught[:200]:
            _rec = (_w.category.__name__, str(_w.message)[:300], _w.lineno)
            if _rec in _trf_seen or len(_trf_seen) >= 20:
                continue
            _trf_seen.add(_rec)
            print("<<<TRF:WARN>>>" + _trf_json.dumps(
                {{"category": _rec[0], "message": _rec[1], "line": str(_rec[2])}}
            ), file=_trf_sys.stderr, flush=True)
        if not ok:
            print("<<<TRF:FAIL>>>", file=_trf_sys.stderr, flush=True)
        _trf_sys.stdout.flush()


_trf_main()
"""


def build_driver(blocks: list[CodeBlock], assertions: dict[str, Assertion]) -> str:
    """Units are (id, source, needs_prev_ok); an assertion snippet is its own `<id>:assert` unit."""
    units: list[tuple[str, str, bool]] = []
    for b in blocks:
        units.append((b.id, strip_magics(b.source), False))
        snippet = assertions.get(b.id, Assertion()).snippet
        if snippet:
            units.append((f"{b.id}:assert", snippet, True))
    return _DRIVER.format(units=units)


_MARK = re.compile(r"^<<<TRF:BEGIN:(?P<id>[^>]+)>>>\r?$", re.MULTILINE)


def split_by_unit(text: str) -> dict[str, str]:
    marks = list(_MARK.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m["id"]] = text[m.end() : end].strip("\r\n")
    return out


_WARN_LINE = re.compile(r"^<<<TRF:WARN>>>(?P<json>.*)$", re.MULTILINE)


def split_warnings(stderr_unit: str) -> tuple[str, list[dict[str, str]]]:
    """(stderr without the warning marker lines, the warnings recorded for this unit)."""
    found: list[dict[str, str]] = []
    for m in _WARN_LINE.finditer(stderr_unit):
        try:
            d = json.loads(m["json"])
            found.append({k: str(d.get(k, "")) for k in ("category", "message", "line")})
        except ValueError:
            continue
    return _WARN_LINE.sub("", stderr_unit).strip("\r\n"), found


_EXC_LINE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Exit|Warning|Interrupt)?:")


def _last_line(tb: str) -> str:
    """The exception line of a traceback (last unindented `Name: message` line)."""
    lines = [ln for ln in tb.strip().splitlines() if ln.strip()]
    for ln in reversed(lines):
        if _EXC_LINE.match(ln) and not ln.startswith("For further information"):
            return ln.strip()
    return lines[-1].strip() if lines else "unknown error"


def interpret(
    blocks: list[CodeBlock],
    assertions: dict[str, Assertion],
    raw: dict,
) -> tuple[list[RunResult], dict[str, tuple[str, str | None]]]:
    """Turn the container's raw output into per-block RunResults and (status, last_error)."""
    outs, errs = split_by_unit(raw["stdout"]), split_by_unit(raw["stderr"])
    results: list[RunResult] = []
    updates: dict[str, tuple[str, str | None]] = {}
    earlier_failure: str | None = None
    started = [uid for uid in outs]
    in_flight = started[-1] if raw.get("timed_out") and started else None

    for b in blocks:
        if b.id not in outs:
            continue  # never started (timeout or crash before reaching it): status unchanged
        err, warned = split_warnings(errs.get(b.id, ""))
        failed = "<<<TRF:FAIL>>>" in err
        err = err.replace("<<<TRF:FAIL>>>", "").strip()
        stdout = outs[b.id][:MAX_BLOCK_OUT]
        timed_out = in_flight == b.id or in_flight == f"{b.id}:assert"
        problem: str | None = None
        a = assertions.get(b.id)
        asserted = bool(a and (a.stdout_contains or a.snippet))

        if timed_out:
            problem = f"timed out after {raw.get('timeout_s', '?')}s"
        elif failed:
            problem = _last_line(err)
        aerr = errs.get(f"{b.id}:assert", "")
        if not problem and "<<<TRF:FAIL>>>" in aerr:
            aerr = aerr.replace("<<<TRF:FAIL>>>", "").strip()
            failed, err, problem = True, aerr, f"assertion failed: {_last_line(aerr)}"
        if not problem and a:
            missing = [s for s in a.stdout_contains if s not in stdout]
            if missing:
                failed, problem = True, f"stdout missing expected text: {missing[0]!r}"
        if problem and earlier_failure and not timed_out:
            problem += f" [after earlier failure in {earlier_failure}; may be a downstream effect]"
        if problem and earlier_failure is None:
            earlier_failure = b.id

        results.append(
            RunResult(
                block_id=b.id,
                exit_code=1 if problem else 0,
                stdout=stdout,
                stderr=err[:MAX_BLOCK_OUT],
                traceback=err if failed and "Traceback" in err else None,
                timed_out=timed_out,
                warnings=warned,
                passed_assertions=None if not asserted else problem is None,
            )
        )
        updates[b.id] = ("failing", problem) if problem else ("passing", None)
    return results, updates


def make_sandbox(
    env_builder: Callable[..., str] = ensure_env_image,
    executor: Callable[[str, SandboxSettings], dict] = run_offline,
    settings: SandboxSettings | None = None,
) -> Callable[[GraphState], dict]:
    """Build the node; builder/executor are injectable so logic is testable without Docker."""

    def sandbox(state: GraphState) -> dict:
        s = settings or SandboxSettings.from_env()
        runnable = [b for b in state.blocks if b.lang == "python"]
        patch: dict = {"loop_count": state.loop_count + 1}
        if not runnable:
            return patch
        reqs, optional = requirements_for(state)
        try:
            tag = env_builder(reqs, optional, s)
        except EnvBuildError as e:
            msg = f"environment build failed: {str(e)[-300:]}"
            patch["blocks"] = [
                b.model_copy(update={"status": "failing", "last_error": msg})
                if b in runnable
                else b
                for b in state.blocks
            ]
            patch["run_results"] = [
                RunResult(block_id=b.id, exit_code=-2, stderr=msg) for b in runnable
            ]
            return patch
        raw = executor(
            build_driver(runnable, state.assertions), s.model_copy(update={"image": tag})
        )
        raw = {**raw, "timeout_s": s.timeout_s}
        results, updates = interpret(runnable, state.assertions, raw)
        if not results:  # container died or timed out before the first block started
            why = "timed out" if raw.get("timed_out") else (raw.get("stderr") or "no output")[-300:]
            msg = f"sandbox produced no per-block output: {why}"
            results = [RunResult(block_id=b.id, exit_code=-1, stderr=msg) for b in runnable]
            updates = {b.id: ("failing", msg) for b in runnable}
        ran = {r.block_id for r in results}
        patch["blocks"] = [
            b.model_copy(update={"status": updates[b.id][0], "last_error": updates[b.id][1]})
            if b.id in updates
            else b
            for b in state.blocks
        ]
        patch["run_results"] = [r for r in state.run_results if r.block_id not in ran] + results
        return patch

    return sandbox


sandbox = make_sandbox()

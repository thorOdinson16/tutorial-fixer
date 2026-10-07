"""In-container entrypoint (stdlib only). stdin: {"code", "timeout_s"} -> stdout: result JSON."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile

TB_MARKER = "Traceback (most recent call last):"
MAX_OUT = 2_000_000


def parse_traceback(stderr: str) -> str | None:
    i = stderr.rfind(TB_MARKER)
    return stderr[i:].strip() if i != -1 else None


def run(code: str, timeout_s: int) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".py", dir="/tmp", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        p = subprocess.run(
            [sys.executable, path], capture_output=True, text=True, timeout=timeout_s
        )
        return {
            "exit_code": p.returncode,
            "stdout": p.stdout[-MAX_OUT:],
            "stderr": p.stderr[-MAX_OUT:],
            "traceback": parse_traceback(p.stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return {
            "exit_code": -1,
            "stdout": out[-MAX_OUT:],
            "stderr": err[-MAX_OUT:],
            "traceback": None,
            "timed_out": True,
        }


if __name__ == "__main__":
    req = json.load(sys.stdin)
    print(json.dumps(run(req["code"], int(req.get("timeout_s", 30)))))

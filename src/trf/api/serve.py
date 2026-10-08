"""One command to run the service: `uv run trf-serve`.

Checks what the pipeline needs (Docker for the sandbox, an LLM configured in the environment or in
`.env`, a retrieval corpus for every registered library), offers to build whatever is missing, then
starts the web service and opens the page.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path

from trf.config import LIBRARIES, ConfigError, LLMSettings, get_cache_dir


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def missing_corpora(cache_dir: Path | None = None) -> list[str]:
    from trf.rag.corpus import corpus_path

    return [lib for lib in LIBRARIES if not corpus_path(lib, cache_dir).exists()]


def check() -> list[str]:
    """Human-readable problems that would make the first job fail. Empty means ready."""
    problems = []
    if not docker_ready():
        problems.append(
            "Docker is not running (start Docker Desktop): code runs only in a sandbox."
        )
    try:
        LLMSettings.from_env()
    except ConfigError as e:
        problems.append(f"No model configured: {e} (put them in .env).")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Tutorial Rot Fixer web service.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--skip-corpus", action="store_true", help="do not build missing corpora")
    args = ap.parse_args()

    problems = check()
    for p in problems:
        print(f"[!] {p}")
    if problems:
        raise SystemExit(1)

    missing = [] if args.skip_corpus else missing_corpora(get_cache_dir())
    if missing:
        print(f"Building retrieval corpora for: {', '.join(missing)} (one time, needs internet)")
        from trf.rag.corpus import build_all

        build_all(missing)

    import uvicorn

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"Serving on {url}  (Ctrl+C to stop)")
    uvicorn.run("trf.api.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()

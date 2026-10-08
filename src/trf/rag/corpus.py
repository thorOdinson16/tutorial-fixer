"""Fetch and chunk docs / changelogs / migration guides per library into a JSONL corpus.

    uv run python -m trf.rag.corpus build [pydantic pandas] [--refresh]

Chunks carry `version_introduced`, so one corpus per library serves every (old -> target) pair:
the retriever filters/boosts by version at query time instead of rebuilding per version.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from trf.config import LIBRARIES, LibraryConfig, get_cache_dir, get_library
from trf.rag.chunking import chunk_document
from trf.state import Chunk, DocType

Fetcher = Callable[[str], str]


def cached_fetcher(cache_dir: Path, *, refresh: bool = False) -> Fetcher:
    http_dir = cache_dir / "http"
    http_dir.mkdir(parents=True, exist_ok=True)

    def fetch(url: str) -> str:
        f = http_dir / (hashlib.sha1(url.encode()).hexdigest() + ".txt")
        if f.exists() and not refresh:
            return f.read_text(encoding="utf-8")
        r = httpx.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        f.write_text(r.text, encoding="utf-8")
        return r.text

    return fetch


_URL_VERSIONS = [  # (pattern, how to build "X.Y.Z" from its groups)
    (r"/v(\d+\.\d+\.\d+)\.rst$", lambda g: g[0]),  # pandas whatsnew/v1.4.0.rst
    (r"/release/(\d+\.\d+\.\d+)-notes\.rst$", lambda g: g[0]),  # numpy
    (r"/whats_new/v(\d+\.\d+)\.rst$", lambda g: f"{g[0]}.0"),  # scikit-learn
    (r"/api_changes_(\d+\.\d+\.\d+)(?:/[\w]+)?\.rst$", lambda g: g[0]),  # matplotlib
    (r"/(?:migration|whatsnew|changelog)_(\d)(\d+)\.rst$", lambda g: f"{g[0]}.{g[1]}.0"),  # sqla
]


def _version_from_url(url: str) -> str | None:
    for pattern, build in _URL_VERSIONS:
        m = re.search(pattern, url)
        if m:
            return build(m.groups())
    return None


def build_corpus(cfg: LibraryConfig, fetch: Fetcher, *, max_chars: int = 1000) -> list[Chunk]:
    plan: list[tuple[DocType, list[str]]] = [
        ("docs", cfg.docs_sources),
        ("changelog", cfg.changelog_sources),
        ("migration", cfg.migration_sources),
    ]
    chunks: list[Chunk] = []
    for doc_type, urls in plan:
        for url in urls:
            default = _version_from_url(url)
            if default is None and doc_type == "migration" and cfg.target_major is not None:
                default = f"{cfg.target_major}.0"  # a v1->v2 guide describes changes in v2.0
            chunks += chunk_document(
                fetch(url),
                library=cfg.name,
                source_url=url,
                doc_type=doc_type,
                default_version=default,
                headings_have_versions=doc_type == "changelog",
                max_chars=max_chars,
            )
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids)), "chunk ids must be unique"
    return chunks


def corpus_path(library: str, cache_dir: Path | None = None) -> Path:
    return (cache_dir or get_cache_dir()) / "corpus" / f"{library}.jsonl"


def save_corpus(chunks: list[Chunk], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(c.model_dump_json() for c in chunks) + "\n", encoding="utf-8")


def load_corpus(path: Path) -> list[Chunk]:
    return [
        Chunk.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_all(names: list[str], *, refresh: bool = False) -> None:
    """Download sources and write one corpus file per library."""
    cache = get_cache_dir()
    fetch = cached_fetcher(cache, refresh=refresh)
    for name in names:
        chunks = build_corpus(get_library(name), fetch)
        save_corpus(chunks, corpus_path(name, cache))
        by_type: dict[str, int] = {}
        for c in chunks:
            by_type[c.doc_type] = by_type.get(c.doc_type, 0) + 1
        print(f"{name}: {len(chunks)} chunks {by_type} -> {corpus_path(name, cache)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["build"])
    ap.add_argument("libraries", nargs="*")
    ap.add_argument("--refresh", action="store_true", help="re-download sources")
    args = ap.parse_args()
    build_all(args.libraries or sorted(LIBRARIES), refresh=args.refresh)


if __name__ == "__main__":
    main()

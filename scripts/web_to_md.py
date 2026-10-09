"""Download tutorial web pages and convert them to Markdown the pipeline can read.

    uv run python scripts/web_to_md.py URL [URL ...] --out runs/real3/md [--no-pin]

The conversion itself lives in `trf.webfetch` (the web service uses it too); see its docstring for
what is kept, dropped and pinned.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from trf.webfetch import PIN_HINTS, convert, doctest_code, fence, ipython_code, slug  # noqa: F401


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--no-pin", action="store_true", help="do not add the version a docs URL names")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    hdr = {"User-Agent": "Mozilla/5.0 (compatible; tutorial-research)"}
    for url in args.urls:
        try:
            r = httpx.get(url, headers=hdr, timeout=40, follow_redirects=True)
        except httpx.HTTPError as e:
            print(f"FAIL {url}: {e}")
            continue
        if r.status_code != 200:
            print(f"FAIL {url}: HTTP {r.status_code}")
            continue
        md = convert(url, r.text, pin=not args.no_pin)
        f = args.out / f"{slug(url)}.md"
        f.write_text(md, encoding="utf-8", newline="\n")
        print(
            f"{f.name}: python={md.count('```python')} text={md.count('```text')} chars={len(md)}"
        )


if __name__ == "__main__":
    sys.exit(main())

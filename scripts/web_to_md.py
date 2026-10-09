"""Download tutorial web pages and convert them to Markdown the pipeline can read.

    uv run python scripts/web_to_md.py URL [URL ...] --out runs/real3/md [--no-pin]

Per page it writes `<slug>.md` with a front matter (title, date if the page has one) and the page's
text with code kept as fenced blocks:

  * a `<pre>` written as an interactive session (`>>> code`, `... continuation`, output lines) is
    turned into plain code (prompts removed, output dropped);
  * any other `<pre>` that parses as Python becomes a ```python block, the rest ```text (printed
    output, shell, configs: never executed);
  * Jupyter magics (`%matplotlib inline`) are tolerated.

Versioned documentation URLs (`/pandas-docs/version/1.3/`, `/doc/1.19/`, `/en/13/`, `/0.24/`,
`/2.2.5/`, `/1.10/`) say which library version the page was written for. Unless `--no-pin`, a
`pip install lib==X` block is put at the top so the pipeline knows the OLD version; the report
says so, because it is information the tool added, not the author.

The downloaded HTML is untrusted data: it is only parsed, never executed.
"""

from __future__ import annotations

import argparse
import ast
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

SKIP = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg", "button"}
BLOCK = {"p", "div", "section", "article", "li", "ul", "ol", "tr", "table", "blockquote", "br"}
MAGIC = re.compile(r"^\s*[!%]", re.M)

# (url regex, pip requirement) for documentation pinned to one version.
PIN_HINTS = [
    (r"pandas\.pydata\.org/pandas-docs/version/1\.3", "pandas==1.3.5"),
    (r"pandas\.pydata\.org/pandas-docs/version/1\.5", "pandas==1.5.3"),
    (r"docs\.pydantic\.dev/1\.10", "pydantic==1.10.13"),
    (r"pydantic-docs\.helpmanual\.io", "pydantic==1.10.13"),
    (r"numpy\.org/doc/1\.19", "numpy==1.19.5"),
    (r"numpy\.org/doc/1\.17", "numpy==1.17.5"),
    (r"docs\.sqlalchemy\.org/en/13", "SQLAlchemy==1.3.24"),
    (r"docs\.sqlalchemy\.org/en/14", "SQLAlchemy==1.4.54"),
    (r"scikit-learn\.org/0\.24", "scikit-learn==0.24.2"),
    (r"scikit-learn\.org/0\.23", "scikit-learn==0.23.2"),
    (r"matplotlib\.org/2\.2\.5", "matplotlib==2.2.5"),
    (r"matplotlib\.org/3\.3\.0", "matplotlib==3.3.0"),
    (r"scipy-1\.10\.0", "scipy==1.10.1"),
    (r"scipy-1\.6\.0", "scipy==1.6.0"),
    (r"scipy-0\.19\.0", "scipy==0.19.0"),
]


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.pre = 0
        self.buf: list[str] = []
        self.heading = 0

    def flush(self) -> None:
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if t:
            self.out.append(("#" * self.heading + " " if self.heading else "") + t + "\n\n")
        self.heading = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif self.skip:
            return
        elif tag == "pre":
            self.flush()
            self.pre += 1
            self.buf = []
        elif re.fullmatch(r"h[1-4]", tag):
            self.flush()
            self.heading = int(tag[1])
        elif tag in BLOCK and not self.pre:
            self.flush()

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
        elif self.skip:
            return
        elif tag == "pre" and self.pre:
            self.pre -= 1
            self.out.append(fence("".join(self.buf)))
            self.buf = []
        elif re.fullmatch(r"h[1-4]", tag) or (tag in BLOCK and not self.pre):
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def close(self) -> None:
        super().close()
        self.flush()


def doctest_code(text: str) -> str | None:
    """The code of an interactive session (`>>> x`, `... y`), without prompts or output."""
    lines = text.strip("\n").splitlines()
    if not any(ln.startswith(">>>") for ln in lines):
        return None
    code: list[str] = []
    for ln in lines:
        if ln.startswith(">>> "):
            code.append(ln[4:])
        elif ln == ">>>":
            code.append("")
        elif ln.startswith("... ") and code:
            code.append(ln[4:])
        elif ln == "..." and code:
            code.append("")
        # anything else is the session's printed output
    return "\n".join(code).strip("\n") or None


NL = chr(10)
IN_PROMPT = re.compile(r"^In \[\d+\]: ?(.*)$")
CONT_PROMPT = re.compile(r"^ +\.\.\.: ?(.*)$")


def ipython_code(text: str) -> str | None:
    """The code of an IPython session (`In [1]: x`, `   ...: y`), without prompts or output."""
    lines = text.strip(NL).splitlines()
    if not any(IN_PROMPT.match(ln) for ln in lines):
        return None
    code: list[str] = []
    in_code = False
    for ln in lines:
        m = IN_PROMPT.match(ln)
        c = CONT_PROMPT.match(ln)
        if m:
            code.append(m.group(1))
            in_code = True
        elif c and in_code:
            code.append(c.group(1))
        else:
            in_code = False  # `Out[n]:` and printed output
    return NL.join(code).strip(NL) or None


def parses(code: str) -> bool:
    try:
        ast.parse(MAGIC.sub("pass #", code))
    except SyntaxError:
        return False
    return True


def fence(raw: str) -> str:
    raw = raw.strip("\n")
    if not raw.strip():
        return ""
    session = doctest_code(raw)
    if session is None:
        session = ipython_code(raw)
    if session is not None:
        return f"```python\n{session}\n```\n\n" if parses(session) else f"```text\n{raw}\n```\n\n"
    return f"```python\n{raw}\n```\n\n" if parses(raw) else f"```text\n{raw}\n```\n\n"


def slug(url: str) -> str:
    s = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:90]


def convert(url: str, raw_html: str, pin: bool = True) -> str:
    p = Page()
    p.feed(raw_html)
    p.close()
    body = re.sub(r"\n{3,}", "\n\n", html.unescape("".join(p.out)))
    title = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.I | re.S)
    title = re.sub(r"\s+", " ", html.unescape(title.group(1))).strip() if title else slug(url)
    d = re.search(
        r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})|published_time"\s+content="(\d{4}-\d{2}-\d{2})',
        raw_html,
    )
    date = next((g for g in d.groups() if g), None) if d else None
    head = (
        f"---\ntitle: {json.dumps(title)[1:-1]}\n" + (f"date: {date}\n" if date else "") + "---\n\n"
    )
    req = next((r for pat, r in PIN_HINTS if re.search(pat, url)), None) if pin else None
    pinned = f"```bash\npip install {req}\n```\n\n" if req else ""
    return head + pinned + body


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

"""Fetch a tutorial web page and convert it to Markdown the pipeline can read.

    md = convert(url, html)        # or: fetch_markdown(url) for the guarded download

The Markdown has a front matter (title, date if the page has one) and the page's
text with code kept as fenced blocks:

  * a `<pre>` written as an interactive session (`>>> code`, `... continuation`, output lines) is
    turned into plain code (prompts removed, output dropped);
  * any other `<pre>` that parses as Python becomes a ```python block, the rest ```text (printed
    output, shell, configs: never executed);
  * Jupyter magics (`%matplotlib inline`) are tolerated.

Versioned documentation URLs (`/pandas-docs/version/1.3/`, `/doc/1.19/`, `/en/13/`, `/0.24/`,
`/2.2.5/`, `/1.10/`) say which library version the page was written for. Unless pinning is off, a
`pip install lib==X` block is put at the top so the pipeline knows the OLD version; the report
says so, because it is information the tool added, not the author.

The downloaded HTML is untrusted data: it is only parsed, never executed.
"""

from __future__ import annotations

import ast
import html
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

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


# --- guarded download (the web service takes a URL from a stranger) -----------------------------

MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
TIMEOUT_S = 20.0
USER_AGENT = "Mozilla/5.0 (compatible; tutorial-rot-fixer)"


class FetchError(Exception):
    """A URL that cannot be used, with a message meant for the person who typed it."""


def _check_host(host: str) -> None:
    """Refuse hosts that resolve to private, loopback or link-local addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError(f"Could not resolve {host}.") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise FetchError("That address is on a private network, so it is not fetched.")


def _check_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Enter a full web address starting with http:// or https://.")
    _check_host(parts.hostname)
    return parts.geturl()


def fetch_markdown(url: str, pin: bool = True) -> tuple[str, str]:
    """Download `url` and return (markdown, title). Raises FetchError with a readable reason.

    Only http(s) to public addresses (re-checked on every redirect), at most MAX_BYTES, HTML,
    Markdown or plain text. The page is parsed, never executed.
    """
    current = _check_url(url)
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=TIMEOUT_S, headers=headers, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", current) as r:
                    if r.is_redirect:
                        loc = r.headers.get("location")
                        if not loc:
                            raise FetchError("The page redirected without saying where.")
                        current = _check_url(urljoin(current, loc))
                        continue
                    if r.status_code != 200:
                        raise FetchError(f"The page answered HTTP {r.status_code}.")
                    kind = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise FetchError("That page is larger than 3 MB.")
                    text = body.decode(r.encoding or "utf-8", errors="replace")
            except httpx.HTTPError as e:
                raise FetchError(f"Could not download the page ({type(e).__name__}).") from e
            break
        else:
            raise FetchError("Too many redirects.")
    if kind in ("text/markdown", "text/x-markdown") or (
        kind == "text/plain" and current.lower().endswith((".md", ".markdown"))
    ):
        title = next(
            (ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), ""
        )
        return text, title or slug(current)
    if kind and "html" not in kind:
        raise FetchError(f"That address is {kind}, not a web page or Markdown.")
    md = convert(current, text, pin=pin)
    m = re.search(r"^title: (.*)$", md, re.M)
    return md, (json.loads(f'"{m.group(1)}"') if m else slug(current))

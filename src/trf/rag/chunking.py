"""Section-aware chunking of Markdown / RST into `Chunk`s with version metadata.

Each chunk = heading path + a run of whole paragraphs/list items (never splits a code fence or a
bullet), so a changelog line like "Removed DataFrame.append, use concat" stays intact and keeps
its section context ("Removal of prior version deprecations/changes").

RST is normalised on the way in: roles (`:meth:`~pandas.DataFrame.append``) become plain
backticked symbols and issue references are dropped, so symbol extraction sees one style.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from trf.state import Chunk, DocType

_VERSION_IN_HEADING = re.compile(r"(?<![\w.])v?(\d+\.\d+(?:\.\d+)?(?:(?:a|b|rc)\d+)?)(?![\w.]*\w)")
_SKIP_HEADINGS = re.compile(r"new contributors|full changelog|^contributors?$", re.I)
_BULLET = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")
_RST_RULE = re.compile(r"^([=\-~^\"'`:#*+_.])\1{2,}\s*$")
_SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_IDENT = re.compile(r"^@?[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*(?:\(\))?$")


@dataclass
class Section:
    path: list[str]
    body: str


# --- parsing -----------------------------------------------------------------------------


def parse_markdown(text: str) -> list[Section]:
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    buf: list[str] = []
    in_fence: str | None = None

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section([h for _, h in path], body))
        buf.clear()

    for line in text.splitlines():
        fm = re.match(r"^\s*(```+|~~~+)", line)
        if fm:
            marker = fm.group(1)[0]
            in_fence = None if in_fence == marker else (in_fence or marker)
        m = None if in_fence else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            flush()
            level = len(m.group(1))
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()
    return sections


def parse_rst(text: str) -> list[Section]:
    lines = text.splitlines()
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    levels: dict[str, int] = {}  # underline char -> depth, by order of first appearance
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section([h for _, h in path], body))
        buf.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        is_title = (
            line.strip()
            and not line.startswith((" ", "\t", ".."))
            and not _RST_RULE.match(line)
            and _RST_RULE.match(nxt)
            and len(nxt.rstrip()) >= len(line.rstrip())
        )
        if is_title:
            flush()
            ch = nxt[0]
            depth = levels.setdefault(ch, len(levels) + 1)
            while path and path[-1][0] >= depth:
                path.pop()
            path.append((depth, line.strip()))
            i += 2
            continue
        buf.append(line)
        i += 1
    flush()
    return sections


_ROLE = re.compile(r":(?:[a-z]+:)?[a-z]+:`([^`]+)`")
_ISSUE_PAREN = re.compile(
    r"\s*\(\s*:(?:issue|pr|ghpull|ghissue):`[^`]*`(?:\s*,\s*:\w+:`[^`]*`)*\s*\)"
)
_ISSUE_ROLE = re.compile(r":(?:issue|pr|ghpull|ghissue):`(\d+)`")


def clean_rst(text: str) -> str:
    text = _ISSUE_PAREN.sub("", text)
    text = _ISSUE_ROLE.sub(r"#\1", text)

    def role(m: re.Match) -> str:
        target = m.group(1)
        if "<" in target and target.endswith(">"):  # :ref:`text <label>` -> text
            return f"`{target.split('<')[0].strip()}`"
        return f"`{target.lstrip('~.')}`"

    text = _ROLE.sub(role, text)
    text = re.sub(r"``([^`\n]+)``", r"`\1`", text)
    text = re.sub(r"^\.\. _[^\n]*:\s*$", "", text, flags=re.M)
    return text


# --- packing -----------------------------------------------------------------------------


def split_units(body: str) -> list[str]:
    """Paragraphs, list items and code fences as indivisible units."""
    units: list[str] = []
    cur: list[str] = []
    in_fence = False

    def flush() -> None:
        if cur:
            units.append("\n".join(cur).strip("\n"))
            cur.clear()

    for line in body.splitlines():
        if re.match(r"^\s*(```+|~~~+)", line):
            if not in_fence:
                flush()
            in_fence = not in_fence
            cur.append(line)
            if not in_fence:
                flush()
            continue
        if in_fence:
            cur.append(line)
        elif not line.strip():
            flush()
        elif _BULLET.match(line):
            flush()
            cur.append(line)
        else:
            cur.append(line)
    flush()
    return [u for u in units if u.strip()]


def pack(units: list[str], max_chars: int) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for u in units:
        if cur and size + len(u) > max_chars:
            out.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(u)
        size += len(u) + 2
    if cur:
        out.append("\n\n".join(cur))
    return out


def primary_symbol(text: str) -> str | None:
    """First backticked, identifier-like span that looks specific (dotted, snake_case or @deco)."""
    for m in _SPAN.finditer(text):
        s = m.group(1).strip()
        if _IDENT.match(s) and ("." in s or "_" in s or s.startswith("@")):
            return s.removesuffix("()")
    return None


def heading_version(path: list[str]) -> str | None:
    for h in path:
        m = _VERSION_IN_HEADING.search(h)
        if m:
            return m.group(1)
    return None


def _slug(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", url.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()).strip("-")


def chunk_document(
    text: str,
    *,
    library: str,
    source_url: str,
    doc_type: DocType,
    default_version: str | None = None,
    headings_have_versions: bool = False,
    max_chars: int = 1000,
    min_chars: int = 40,
) -> list[Chunk]:
    rst = source_url.endswith(".rst")
    sections = parse_rst(clean_rst(text)) if rst else parse_markdown(text)
    key = hashlib.sha1(source_url.encode()).hexdigest()[:6]
    slug = _slug(source_url)
    chunks: list[Chunk] = []
    for sec in sections:
        if any(_SKIP_HEADINGS.search(h) for h in sec.path):
            continue
        # Only trust headings as versions where the source is organised by release; elsewhere
        # a heading like "Python 3.8 support" would be mistaken for a library version.
        version = default_version or (heading_version(sec.path) if headings_have_versions else None)
        header = f"{library} {version or ''} > ".replace("  ", " ") + " > ".join(sec.path)
        for piece in pack(split_units(sec.body), max_chars):
            if len(piece.strip()) < min_chars:
                continue
            chunks.append(
                Chunk(
                    id=f"{library}:{slug}-{key}:{len(chunks):04d}",
                    text=f"{header}\n\n{piece}".strip(),
                    library=library,
                    version_introduced=version,
                    symbol=primary_symbol(piece),
                    doc_type=doc_type,
                    source_url=source_url,
                )
            )
    return chunks

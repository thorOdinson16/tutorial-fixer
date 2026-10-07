from rag_fixtures import MD, MIGRATION_MD, RST
from trf.config import LibraryConfig
from trf.rag.chunking import (
    chunk_document,
    clean_rst,
    parse_markdown,
    parse_rst,
    primary_symbol,
    split_units,
)
from trf.rag.corpus import build_corpus, load_corpus, save_corpus


def test_markdown_headings_ignore_code_fence_comments():
    secs = parse_markdown("# A\n\n## B\ntext\n\n```python\n# not a heading\nx = 1\n```\n")
    assert secs[-1].path == ["A", "B"]
    assert "# not a heading" in secs[-1].body


def test_rst_headings_by_underline_order():
    secs = parse_rst(clean_rst(RST))
    paths = [s.path for s in secs]
    assert all(p[0].startswith("What's new in 2.0.0") for p in paths)  # title is the root
    assert ["What's new in 2.0.0 (April 3, 2023)", "Enhancements"] in paths
    assert any(p[-1] == "Removal of prior version deprecations/changes" for p in paths)


def test_clean_rst_normalises_roles_and_drops_issue_refs():
    out = clean_rst(
        "- Removed :meth:`~pandas.DataFrame.append`, use :func:`concat` (:issue:`35407`)\n``x``"
    )
    assert "`pandas.DataFrame.append`" in out and "`concat`" in out
    assert "issue" not in out and out.endswith("`x`")


def test_split_units_keeps_fences_and_bullets_whole():
    units = split_units("intro\n\n- one\n  cont\n- two\n\n```py\na = 1\n\nb = 2\n```\n")
    assert units == ["intro", "- one\n  cont", "- two", "```py\na = 1\n\nb = 2\n```"]


def test_primary_symbol():
    assert primary_symbol("use `DataFrame.append` or `str`") == "DataFrame.append"
    assert primary_symbol("only `str` and `int`") is None
    assert primary_symbol("`@validator` is gone") == "@validator"


def test_chunk_versions_from_rst_default_and_md_headings():
    rst = chunk_document(
        RST, library="pandas", source_url="https://x/v2.0.0.rst", doc_type="migration",
        default_version="2.0.0",
    )  # fmt: skip
    assert rst and all(c.version_introduced == "2.0.0" and c.doc_type == "migration" for c in rst)
    removal = next(c for c in rst if "iteritems" in c.text)
    assert "Removal of prior version" in removal.text and removal.text.startswith("pandas 2.0.0 >")
    assert "(:issue" not in removal.text

    md = chunk_document(
        MD, library="pydantic", source_url="https://x/HISTORY.md", doc_type="changelog",
        headings_have_versions=True,
    )  # fmt: skip
    vers = {c.version_introduced for c in md}
    assert vers == {"2.5.0", "1.10.13"}
    assert not any("newbie" in c.text for c in md)  # New Contributors skipped


def test_headings_not_trusted_as_versions_by_default():
    cs = chunk_document(
        "# Python 3.8 support\n\nwe dropped support for the old interpreter completely.\n",
        library="x", source_url="https://x/a.md", doc_type="docs",
    )  # fmt: skip
    assert cs[0].version_introduced is None


def test_pack_respects_max_chars_without_splitting_items():
    body = "\n".join(f"- item number {i} " + "x" * 80 for i in range(20))
    cs = chunk_document(
        "# T\n\n" + body, library="x", source_url="https://x/a.md", doc_type="docs", max_chars=300
    )
    assert len(cs) > 3
    assert all(c.text.count("- item") >= 1 for c in cs)
    assert sum(c.text.count("- item") for c in cs) == 20


def test_build_corpus_with_fake_fetcher_and_roundtrip(tmp_path):
    cfg = LibraryConfig(
        name="lib", pypi_name="lib", target_major=2,
        docs_sources=[], changelog_sources=["https://x/HISTORY.md"],
        migration_sources=["https://x/migration.md", "https://x/v2.0.0.rst"],
    )  # fmt: skip
    pages = {
        "https://x/HISTORY.md": MD,
        "https://x/migration.md": MIGRATION_MD,
        "https://x/v2.0.0.rst": RST,
    }
    chunks = build_corpus(cfg, pages.__getitem__)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))
    mig = [c for c in chunks if c.source_url.endswith("migration.md")]
    assert mig and all(c.version_introduced == "2.0" and c.doc_type == "migration" for c in mig)
    assert any(
        c.source_url.endswith("v2.0.0.rst") and c.version_introduced == "2.0.0" for c in chunks
    )
    assert build_corpus(cfg, pages.__getitem__) == chunks  # deterministic ids

    p = tmp_path / "c" / "lib.jsonl"
    save_corpus(chunks, p)
    assert load_corpus(p) == chunks

import numpy as np
import pytest

from rag_fixtures import FakeEncoder, FakeScorer, chunk
from trf.rag.bm25 import BM25Index, tokenize
from trf.rag.dense import DenseIndex
from trf.rag.hybrid import rrf
from trf.rag.rerank import applies_to_target, rerank, version_proximity
from trf.rag.symbol_index import SymbolIndex, names_in_chunk
from trf.state import Symbol

CHUNKS = [
    chunk("a", "`parse_obj` renamed to `model_validate`"),
    chunk("b", "Removed `DataFrame.append`, use `concat` instead"),
    chunk("c", "Unrelated note about performance of the parser"),
    chunk("d", "Removed `Series.iteritems`, use `Series.items`"),
]


def test_tokenize_keeps_identifiers_and_dotted_parts():
    toks = tokenize("pandas.DataFrame.append(parse_obj)")
    for t in ["pandas.dataframe.append", "dataframe.append", "append", "parse_obj", "pandas"]:
        assert t in toks


def test_bm25_exact_identifier_wins():
    idx = BM25Index([c.id for c in CHUNKS], [c.text for c in CHUNKS])
    assert idx.search("parse_obj")[0][0] == "a"
    assert idx.search("pandas.DataFrame.append")[0][0] == "b"
    assert idx.search("zzz_unknown") == []
    assert BM25Index([], []).search("x") == []


def test_dense_ranks_by_similarity_and_caches(tmp_path):
    enc = FakeEncoder()
    ids, texts = [c.id for c in CHUNKS], [c.text for c in CHUNKS]
    idx = DenseIndex.build(ids, texts, enc, tmp_path, "t")
    assert idx.search("removed DataFrame append concat", k=2)[0][0] == "b"
    calls = enc.calls
    DenseIndex.build(ids, texts, enc, tmp_path, "t")  # second build loads the .npy
    assert enc.calls == calls
    assert np.allclose(np.linalg.norm(idx.matrix, axis=1), 1.0)


def test_rrf_math_and_determinism():
    out = dict(rrf([["a", "b"], ["b", "c"]], k=60))
    assert out["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert out["a"] == pytest.approx(1 / 61) and out["c"] == pytest.approx(1 / 62)
    assert rrf([["a", "b"], ["b", "a"]])[0][0] == "a"  # tie -> id order
    assert rrf([["a", "a", "a"]]) == [("a", 1 / 61)]  # duplicates in one list count once
    w = dict(rrf([["a"], ["b"]], weights=[2.0, 1.0]))
    assert w["a"] > w["b"]
    with pytest.raises(ValueError):
        rrf([["a"]], weights=[1, 2])


def test_version_proximity_cases():
    mk = lambda v, t="changelog": chunk("x", "t", v, t)  # noqa: E731
    assert version_proximity(mk("2.0.0"), "1.3.5", "2.3.3") == 1.0
    assert version_proximity(mk("1.0.0"), "1.3.5", "2.3.3") == -0.25  # already true for old
    assert version_proximity(mk("3.0.0"), "1.3.5", "2.3.3") == -1.0
    assert version_proximity(mk("2.0.0"), None, "2.3.3") == 0.5  # same major as target
    assert version_proximity(mk(None, "migration"), None, "2.3.3") == 0.25
    assert version_proximity(mk(None, "docs"), "1", "2") == 0.0
    assert (
        version_proximity(mk("2.14.0b2"), "1.10.13", "2.13.5") == -1.0
    )  # pre-release after target
    assert applies_to_target(mk("2.0.0"), "2.3.3") and not applies_to_target(mk("3.0"), "2.3.3")
    assert applies_to_target(mk(None), "2.3.3")


def test_rerank_boost_changes_order_but_scorer_dominates_without_it():
    old_chunk = chunk("old", "append concat removed", "1.0.0")
    new_chunk = chunk("new", "append concat removed", "2.0.0")
    cands = [(old_chunk, 1.0), (new_chunk, 0.9)]
    plain = rerank("append concat", cands, scorer=FakeScorer(), use_version_boost=False, blend=0.0)
    assert plain[0][0].id == "new"  # identical scores -> id order, no boost
    boosted = rerank("append concat", cands, scorer=FakeScorer(), use_version_boost=True,
                     old="1.3.5", target="2.3.3")  # fmt: skip
    assert boosted[0][0].id == "new" and boosted[0][1] > boosted[1][1]
    no_scorer = rerank("q", cands, scorer=None, use_version_boost=False)
    assert [c.id for c, _ in no_scorer] == ["old", "new"]  # keeps upstream order
    assert rerank("q", [], scorer=None, use_version_boost=True) == []


def test_symbol_index_extraction_and_lookup():
    assert {"DataFrame.append", "concat"} <= names_in_chunk("`DataFrame.append` -> `concat()`")
    assert "User.parse_obj" in names_in_chunk("```python\nUser.parse_obj(data)\n```")
    idx = SymbolIndex(CHUNKS)
    hit = idx.lookup(Symbol(name="pandas.DataFrame.append", library="pandas"))
    assert hit[0][0] == "b"
    assert idx.lookup(Symbol(name="pydantic.BaseModel.parse_obj", library="pydantic"))[0][0] == "a"
    assert idx.lookup(Symbol(name="pandas.read_csv", library="pandas")) == []
    assert idx.lookup(Symbol(name="pandas.*", library="pandas")) == []


def test_symbol_index_argument_prefers_callee_chunks():
    cs = [
        chunk("f", "`Field` argument `regex` is now `pattern`"),
        chunk("g", "The `regex` module is used internally"),
    ]
    hits = SymbolIndex(cs).lookup(
        Symbol(name="pydantic.Field(regex)", library="pydantic", kind="argument")
    )
    assert [h[0] for h in hits][0] == "f"


def test_symbol_index_discounts_generic_names():
    cs = [chunk(f"c{i}", "uses `dict` here") for i in range(30)] + [
        chunk("r", "`orm_mode` renamed")
    ]
    idx = SymbolIndex(cs)
    generic = idx.lookup(Symbol(name="pydantic.BaseModel.dict", library="pydantic"))[0][1]
    rare = idx.lookup(Symbol(name="pydantic.BaseModel.Config.orm_mode", library="pydantic"))[0][1]
    assert rare > generic


def test_rerank_blend_keeps_upstream_evidence():
    strong_upstream = chunk("u", "x", "2.0.0")
    strong_ce = chunk("c", "append concat", "2.0.0")
    cands = [(strong_upstream, 1.0), (strong_ce, 0.1)]
    kw = dict(scorer=FakeScorer(), use_version_boost=False)
    assert rerank("append concat", cands, blend=0.0, **kw)[0][0].id == "c"  # CE alone
    assert rerank("append concat", cands, blend=0.8, **kw)[0][0].id == "u"  # upstream counts too


def test_symbol_index_prefers_library_qualified_key():
    cs = [chunk(f"n{i}", "uses `datetime` objects") for i in range(20)] + [
        chunk("t", "Removed `pandas.datetime`, use `datetime.datetime`")
    ]
    hits = SymbolIndex(cs).lookup(Symbol(name="pandas.datetime", library="pandas"))
    assert hits[0][0] == "t"

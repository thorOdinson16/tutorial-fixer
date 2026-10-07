from rag_fixtures import FakeEncoder, FakeScorer, chunk
from trf.config import PipelineFlags
from trf.nodes.retrieve import make_retrieve
from trf.rag.pipeline import RetrievalPipeline, symbol_queries
from trf.state import GraphState, Symbol

CHUNKS = [
    chunk(
        "append", "pandas 2.0.0 > Removed `DataFrame.append`, use `concat`", "2.0.0", lib="pandas"
    ),
    chunk("append-old", "pandas 1.0.0 > Deprecated `DataFrame.append` slow", "1.0.0", lib="pandas"),
    chunk("future", "pandas 3.0.0 > `DataFrame.append` new thing `concat`", "3.0.0", lib="pandas"),
    chunk("items", "pandas 2.0.0 > Removed `Series.iteritems`, use `items`", "2.0.0", lib="pandas"),
    chunk("noise", "pandas 2.0.0 > Faster parsing of csv files", "2.0.0", lib="pandas"),
]
SYM = Symbol(name="pandas.DataFrame.append", library="pandas", kind="method")


def flags(**kw):
    base = {f: False for f in PipelineFlags.model_fields}
    return PipelineFlags(**{**base, **kw})


def pipe(f, enc=None, sc=None):
    return RetrievalPipeline(CHUNKS, f, encoder=enc or FakeEncoder(), scorer=sc or FakeScorer())


def ids(hits):
    return [h.chunk.id for h in hits]


def test_llm_only_config_retrieves_nothing():
    assert pipe(flags()).retrieve_for_symbol(SYM, old="1.3.5", target="2.3.3") == []


def test_stages_are_lazy_and_toggleable():
    enc, sc = FakeEncoder(), FakeScorer()
    p = pipe(flags(use_bm25=True), enc, sc)
    hits = p.retrieve_for_symbol(SYM, old="1.3.5", target="2.3.3")
    assert hits and enc.calls == 0 and sc.calls == 0  # no dense, no rerank => models untouched
    assert all(h.via == ["bm25"] for h in hits)
    assert p._dense is None and p._symbols is None

    p = pipe(flags(use_dense=True, use_rerank=True), enc, sc)
    p.retrieve_for_symbol(SYM, old="1.3.5", target="2.3.3")
    assert enc.calls > 0 and sc.calls == 1


def test_version_filter_only_with_boost_flag():
    on = pipe(flags(use_bm25=True, use_version_boost=True))
    off = pipe(flags(use_bm25=True))
    assert "future" not in ids(on.retrieve_for_symbol(SYM, old="1.3.5", target="2.3.3"))
    assert "future" in ids(off.retrieve_for_symbol(SYM, old="1.3.5", target="2.3.3", k=10))


def test_version_boost_prefers_transition_chunk():
    p = pipe(flags(use_bm25=True, use_version_boost=True))
    got = ids(p.retrieve_for_symbol(SYM, old="1.3.5", target="2.3.3"))
    assert got.index("append") < got.index("append-old")


def test_symbol_index_contributes_and_is_labelled():
    p = pipe(flags(use_symbol_index=True))
    hits = p.retrieve_for_symbol(SYM, old="1.3.5", target="2.3.3")
    assert set(ids(hits)) >= {"append", "append-old"}
    assert all("symbol_index" in h.via for h in hits)


def test_hybrid_labels_both_retrievers():
    p = pipe(flags(use_bm25=True, use_dense=True))
    top = p.retrieve_for_symbol(SYM, old="1.3.5", target="2.3.3")[0]
    assert set(top.via) == {"bm25", "dense"}


def test_k_limits_results():
    assert len(pipe(flags(use_bm25=True)).retrieve("append concat items", k=2)) == 2


def test_symbol_queries():
    kw, nl = symbol_queries(SYM)
    assert kw == "DataFrame.append" and "DataFrame.append" in nl
    arg = Symbol(name="pydantic.Field(regex)", library="pydantic", kind="argument")
    assert symbol_queries(arg)[0] == "regex Field"
    assert symbol_queries(Symbol(name="pandas.*.append", library="pandas"))[0] == "append"
    # a bare top-level name is qualified: `pandas.datetime` is rarer (and what chunks say)
    assert symbol_queries(Symbol(name="pandas.datetime", library="pandas"))[0] == "pandas.datetime"
    cfg = Symbol(name="pydantic.BaseModel.Config.orm_mode", library="pydantic", kind="config_key")
    assert "Config.orm_mode" in symbol_queries(cfg)[1] and "BaseModel" not in symbol_queries(cfg)[1]


def test_retrieve_node_fills_evidence_and_skips_other_libs():
    p = pipe(flags(use_bm25=True))
    node = make_retrieve(factory=lambda lib: p)
    state = GraphState(
        symbols=[SYM, Symbol(name="numpy.array", library="numpy")],
        old_versions={"pandas": "1.3.5"},
        target_versions={"pandas": "2.3.3"},
    )
    ev = node(state)["evidence"]
    assert set(ev) == {"pandas.DataFrame.append"}
    assert ev["pandas.DataFrame.append"][0].id in {"append", "append-old"}

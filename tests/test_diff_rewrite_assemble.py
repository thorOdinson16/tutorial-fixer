"""diff / rewrite / assemble with a scripted fake LLM (no network, no Docker)."""

import pytest

from rag_fixtures import chunk
from trf.config import PipelineFlags
from trf.nodes.assemble import assemble, build_change_log, render
from trf.nodes.diff import DiffBatch, VerdictOut, make_diff
from trf.nodes.extract import extract
from trf.nodes.ingest import ingest
from trf.nodes.rewrite import (
    ChangeOut,
    RewriteOut,
    add_packages_to_install,
    make_rewrite,
    match_verdict,
    parses,
    strip_fences,
    update_pins,
)
from trf.state import ChangeLogEntry, DiffVerdict, GraphState, Symbol

RETRIEVAL = PipelineFlags(use_bm25=True, use_dense=False, use_symbol_index=False)
NO_RETRIEVAL = PipelineFlags(
    use_bm25=False, use_dense=False, use_symbol_index=False, use_rerank=False
)

MD = """# T

```bash
pip install pandas==1.3.5
```

Add a row:

```python
import pandas as pd
df = pd.DataFrame({"a": [1]})
df = df.append({"a": 2}, ignore_index=True)
print(len(df))
```
"""


class FakeLLM:
    """Returns scripted objects in order; records prompts."""

    def __init__(self, *outs):
        self.outs = list(outs)
        self.prompts: list[str] = []

    def structured(self, prompt, schema, *, system=None, max_retries=2):
        self.prompts.append(prompt)
        out = self.outs.pop(0)
        assert isinstance(out, schema), (type(out), schema)
        return out


def prepared(md=MD, **extra) -> GraphState:
    s = GraphState(tutorial_md=md, **extra)
    s = s.model_copy(update=ingest(s))
    s = s.model_copy(update=extract(s))
    return s.model_copy(
        update={"old_versions": {"pandas": "1.3.5"}, "target_versions": {"pandas": "2.3.3"}}
    )


EV = chunk("pandas:wn:1", "Removed `DataFrame.append`, use `concat`", "2.0.0", lib="pandas")


def with_evidence(s: GraphState) -> GraphState:
    return s.model_copy(update={"evidence": {"pandas.DataFrame.append": [EV]}})


# --- diff ---------------------------------------------------------------------------------


def test_diff_keeps_only_cited_changed_verdicts():
    s = with_evidence(prepared())
    llm = FakeLLM(
        DiffBatch(
            verdicts=[
                VerdictOut(symbol="pandas.DataFrame.append", verdict="removed",
                           replacement="pandas.concat", evidence_chunk_ids=["pandas:wn:1", "made-up"],
                           confidence=0.9),
                VerdictOut(symbol="pandas.DataFrame", verdict="unchanged",
                           evidence_chunk_ids=["pandas:wn:1"], confidence=0.9),
                VerdictOut(symbol="pandas.nonexistent", verdict="removed",
                           evidence_chunk_ids=["pandas:wn:1"], confidence=0.9),
            ]
        )
    )  # fmt: skip
    out = make_diff(llm, RETRIEVAL)(s)
    assert [(v.symbol, v.verdict, v.evidence_chunk_ids) for v in out["verdicts"]] == [
        ("pandas.DataFrame.append", "removed", ["pandas:wn:1"])  # fabricated id stripped
    ]
    assert any("unknown symbol" in w for w in out["warnings"])
    assert (
        "pandas:wn:1" in llm.prompts[0] and "1.3.5" in llm.prompts[0] and "2.3.3" in llm.prompts[0]
    )


@pytest.mark.parametrize(
    "verdict",
    [
        VerdictOut(symbol="pandas.DataFrame.append", verdict="removed",
                   evidence_chunk_ids=["not-shown"], confidence=0.9),
        VerdictOut(symbol="pandas.DataFrame.append", verdict="removed",
                   evidence_chunk_ids=[], confidence=0.9),
        VerdictOut(symbol="pandas.DataFrame.append", verdict="removed",
                   evidence_chunk_ids=["pandas:wn:1"], confidence=0.2),
    ],
)  # fmt: skip
def test_diff_drops_uncited_or_low_confidence(verdict):
    out = make_diff(FakeLLM(DiffBatch(verdicts=[verdict])), RETRIEVAL)(with_evidence(prepared()))
    assert out["verdicts"] == [] and out["warnings"]


def test_diff_skipped_without_retrieval_or_evidence():
    s = with_evidence(prepared())
    assert make_diff(FakeLLM(), NO_RETRIEVAL)(s) == {}  # LLM-only: nothing to cite
    out = make_diff(FakeLLM(), RETRIEVAL)(prepared())  # no evidence -> no LLM call
    assert out["verdicts"] == []


# --- rewrite ------------------------------------------------------------------------------

V = DiffVerdict(symbol="pandas.DataFrame.append", verdict="removed", replacement="pandas.concat",
                evidence_chunk_ids=["pandas:wn:1"], confidence=0.9)  # fmt: skip
GOOD = RewriteOut(
    code='import pandas as pd\ndf = pd.DataFrame({"a": [1]})\n'
    'df = pd.concat([df, pd.DataFrame([{"a": 2}])], ignore_index=True)\nprint(len(df))',
    changes=[ChangeOut(symbol="pandas.DataFrame.append", description="append -> concat")],
)


def test_rewrite_applies_cited_change_and_pin():
    s = with_evidence(prepared()).model_copy(update={"verdicts": [V]})
    llm = FakeLLM(GOOD)
    out = make_rewrite(llm, RETRIEVAL)(s)
    b = {x.id: x for x in out["blocks"]}
    assert "pd.concat" in b["b2"].source and b["b2"].source.endswith("\n")  # newline preserved
    assert b["b2"].original_source == s.blocks[1].source and b["b2"].status == "pending"
    assert b["b1"].source == "pip install pandas==2.3.3\n"  # deterministic pin update
    kinds = {(e.block_id, e.kind) for e in out["change_log"]}
    assert kinds == {("b2", "api_change"), ("b1", "version_pin")}
    api = next(e for e in out["change_log"] if e.kind == "api_change")
    assert api.evidence_chunk_ids == ["pandas:wn:1"] and api.source_urls == [
        "https://x/pandas:wn:1"
    ]
    assert "pandas:wn:1" in llm.prompts[0] and "use `pandas.concat`" in llm.prompts[0]


def test_rewrite_only_touches_affected_blocks():
    s = with_evidence(prepared()).model_copy(update={"verdicts": []})
    out = make_rewrite(FakeLLM(), RETRIEVAL)(s)  # no verdicts -> no LLM call at all
    assert out["blocks"][1].source == s.blocks[1].source


def test_rewrite_rejects_unparseable_then_retries():
    bad = RewriteOut(code="df = (", changes=[ChangeOut(symbol=V.symbol)])
    s = with_evidence(prepared()).model_copy(update={"verdicts": [V]})
    llm = FakeLLM(bad, GOOD)
    out = make_rewrite(llm, RETRIEVAL)(s)
    assert "pd.concat" in out["blocks"][1].source
    assert "syntax error" in llm.prompts[1]
    out = make_rewrite(FakeLLM(bad, bad), RETRIEVAL)(s)
    assert out["blocks"][1].source == s.blocks[1].source
    assert any("unparseable" in w for w in out["warnings"])


def test_rewrite_rejects_change_without_attributable_citation():
    s = with_evidence(prepared()).model_copy(
        update={"verdicts": [V, V.model_copy(update={"symbol": "pandas.DataFrame"})]}
    )
    # an edit that touches only the print line: none of the block's verdicts appears in it
    edited = s.blocks[1].source.replace("print(len(df))", "print(len(df) + 0)")
    assert edited != s.blocks[1].source
    unattributed = RewriteOut(code=edited, changes=[ChangeOut(symbol="something.else")])
    out = make_rewrite(FakeLLM(unattributed), RETRIEVAL)(s)
    assert out["blocks"][1].source == s.blocks[1].source  # kept: edit could not be cited
    assert any("without cited evidence" in w for w in out["warnings"])


def test_rewrite_identical_code_is_no_change():
    s = with_evidence(prepared()).model_copy(update={"verdicts": [V]})
    same = RewriteOut(code=s.blocks[1].source, changes=[])
    out = make_rewrite(FakeLLM(same), RETRIEVAL)(s)
    assert [e.kind for e in out["change_log"]] == ["version_pin"]


def test_rewrite_llm_only_mode_labels_edits_uncited():
    s = prepared()  # no evidence, no verdicts
    llm = FakeLLM(GOOD)
    out = make_rewrite(llm, NO_RETRIEVAL)(s)
    assert "pd.concat" in out["blocks"][1].source
    api = [e for e in out["change_log"] if e.block_id == "b2"]
    assert api and all(e.kind == "llm_prior" and not e.evidence_chunk_ids for e in api)
    assert "EVIDENCE" not in llm.prompts[0]


def test_rewrite_adds_new_dependency_to_install_line_with_inherited_citation():
    md = (
        "```bash\npip install pydantic==1.10.13\n```\n"
        "```python\nfrom pydantic import BaseSettings\nclass S(BaseSettings):\n    x: int = 1\n```\n"
    )
    s = prepared(md).model_copy(
        update={"old_versions": {"pydantic": "1.10.13"}, "target_versions": {"pydantic": "2.13.5"},
                "libraries": ["pydantic"]}
    )  # fmt: skip
    ev = chunk("pydantic:m:1", "BaseSettings moved to `pydantic-settings`", "2.0", lib="pydantic")
    v = DiffVerdict(symbol="pydantic.BaseSettings", verdict="removed",
                    replacement="pydantic_settings.BaseSettings", evidence_chunk_ids=["pydantic:m:1"],
                    confidence=0.9)  # fmt: skip
    s = s.model_copy(update={"verdicts": [v], "evidence": {"pydantic.BaseSettings": [ev]}})
    new = RewriteOut(
        code="from pydantic_settings import BaseSettings\nclass S(BaseSettings):\n    x: int = 1\n",
        changes=[ChangeOut(symbol="pydantic.BaseSettings", description="moved")],
    )
    out = make_rewrite(FakeLLM(new), RETRIEVAL)(s)
    install = out["blocks"][0].source
    assert install == "pip install pydantic==2.13.5 pydantic-settings\n"
    dep = next(e for e in out["change_log"] if e.kind == "dependency")
    assert dep.evidence_chunk_ids == ["pydantic:m:1"]
    assert "pydantic-settings" in out["extra_packages"]  # so the sandbox installs it


def test_update_pins():
    t = {"pandas": ("pandas", "2.3.3"), "pydantic": ("pydantic", "2.13.5")}
    src = 'pip install pandas==1.3.5 "pydantic<2" numpy==1.26 -q\npandas>=1.0\nrequests==2.0\n'
    new, ch = update_pins(src, t)
    assert (
        new
        == 'pip install pandas==2.3.3 "pydantic==2.13.5" numpy==1.26 -q\npandas>=1.0\nrequests==2.0\n'
    )
    assert sorted(ch) == [("pandas", "==1.3.5", "2.3.3"), ("pydantic", "<2", "2.13.5")]
    assert update_pins("pip install pandas>=1.0", t)[1] == []  # already satisfied: untouched
    assert update_pins("print('pandas==1.0')", t)[1] == []  # not an install line


def test_add_packages_to_install():
    assert add_packages_to_install("pip install a\n", ["b"]) == ("pip install a b\n", True)
    assert add_packages_to_install("pip install a b\n", ["b"]) == ("pip install a b\n", True)
    assert add_packages_to_install("echo hi\n", ["b"])[1] is False
    assert add_packages_to_install("pip install a && echo\n", ["b"])[1] is False  # too complex


def test_helpers():
    assert strip_fences("```python\nx = 1\n```") == "x = 1"
    assert strip_fences("x = 1") == "x = 1"
    assert parses("x = 1") is None and "line" in parses("x = (")
    assert parses("!pip install x\nimport os") is None  # magics tolerated
    vs = {"pydantic.BaseModel.parse_obj": V}
    assert match_verdict("parse_obj", vs) is V and match_verdict(None, vs) is None
    assert match_verdict("dict", vs) is None


# --- assemble -----------------------------------------------------------------------------


def test_assemble_roundtrips_prose_and_marks_failures_unresolved():
    s = prepared()
    blocks = [
        s.blocks[0],
        s.blocks[1].model_copy(update={"source": "print('new')\n", "status": "failing",
                                       "last_error": "KeyError: 'x'"}),
    ]  # fmt: skip
    s = s.model_copy(update={"blocks": blocks})
    out = assemble(s)
    assert [b.status for b in out["blocks"]] == ["pending", "unresolved"]
    assert out["unresolved_block_ids"] == ["b2"]
    assert out["output_md"] == MD.replace(
        'import pandas as pd\ndf = pd.DataFrame({"a": [1]})\ndf = df.append({"a": 2}, ignore_index=True)\nprint(len(df))\n',
        "print('new')\n",
    )
    assert "UNRESOLVED" in out["change_log_md"] and "KeyError: 'x'" in out["change_log_md"]


def test_assemble_unchanged_run_reproduces_input_exactly():
    s = prepared()
    assert assemble(s)["output_md"] == MD


def test_change_log_shows_citations_diff_and_not_executed():
    s = with_evidence(prepared()).model_copy(update={"verdicts": [V]})
    s = s.model_copy(update=make_rewrite(FakeLLM(GOOD), RETRIEVAL)(s))
    s = s.model_copy(
        update={
            "blocks": [
                b.model_copy(update={"status": "passing"}) if b.lang == "python" else b
                for b in s.blocks
            ]
        }
    )
    md = build_change_log(s, s.blocks)
    assert "pandas 1.3.5 → 2.3.3" in md and "1/1 passing" in md
    assert "not executed" in md  # the bash block
    assert "`pandas:wn:1`" in md and "[source](https://x/pandas:wn:1)" in md
    assert "```diff" in md and "-df = df.append" in md and "+df = pd.concat" in md
    assert "pypi.org/project/pandas/2.3.3" in md


def test_render_keeps_segments_without_block():
    s = prepared()
    assert render(s.segments, {}) == MD


def test_changelog_entry_still_requires_citation():
    with pytest.raises(ValueError):
        ChangeLogEntry(block_id="b", description="x")
    assert Symbol  # imported for completeness


def test_diff_batches_symbols_and_survives_an_llm_failure():
    from trf.llm import LLMError
    from trf.state import Symbol

    n = 8  # 2 batches of 6 and 2
    syms = [Symbol(name=f"pandas.f{i}", library="pandas") for i in range(n)]
    ev = {s.name: [chunk(f"c{i}", f"text {i}", "2.0.0", lib="pandas")] for i, s in enumerate(syms)}
    s = prepared().model_copy(update={"symbols": syms, "evidence": ev})

    class Flaky(FakeLLM):
        def structured(self, prompt, schema, *, system=None, max_retries=2):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise LLMError("empty reply")
            return self.outs.pop(0)

    llm = Flaky(
        DiffBatch(
            verdicts=[
                VerdictOut(symbol="pandas.f6", verdict="removed", evidence_chunk_ids=["c6"],
                           confidence=0.9)
            ]
        )
    )  # fmt: skip
    out = make_diff(llm, RETRIEVAL)(s)
    assert len(llm.prompts) == 2
    assert "pandas.f0" in llm.prompts[0] and "pandas.f6" not in llm.prompts[0]
    assert "pandas.f6" in llm.prompts[1] and "pandas.f0" not in llm.prompts[1]
    assert [v.symbol for v in out["verdicts"]] == ["pandas.f6"]  # batch 2 still produced a verdict
    assert any("LLM failed" in w for w in out["warnings"])  # batch 1 failure is recorded


def test_diff_accepts_symbols_echoed_with_their_kind_suffix():
    from trf.nodes.diff import clean_symbol

    known = {"pandas.DataFrame.append", "pandas.read_csv(parse_dates)"}
    assert clean_symbol("pandas.DataFrame.append (method)", known) == "pandas.DataFrame.append"
    assert (
        clean_symbol("`pandas.read_csv(parse_dates)` (argument)", known)
        == "pandas.read_csv(parse_dates)"
    )
    assert clean_symbol("PANDAS.dataframe.APPEND", known) == "pandas.DataFrame.append"
    assert clean_symbol("pandas.read_csv(parse_dates)", known) == "pandas.read_csv(parse_dates)"
    assert clean_symbol("pandas.Nonexistent (method)", known) == "pandas.Nonexistent (method)"

    s = with_evidence(prepared())
    llm = FakeLLM(
        DiffBatch(
            verdicts=[
                VerdictOut(
                    symbol="pandas.DataFrame.append (method)",
                    verdict="removed",
                    replacement="pandas.concat",
                    evidence_chunk_ids=["pandas:wn:1"],
                    confidence=0.9,
                )
            ]
        )
    )
    out = make_diff(llm, RETRIEVAL)(s)
    assert [v.symbol for v in out["verdicts"]] == ["pandas.DataFrame.append"]


def test_snippet_centres_on_the_symbol_instead_of_cutting_the_head():
    from trf.nodes.diff import snippet, symbol_terms

    filler = "Removed unrelated thing number one. " * 25
    text = filler + "Removed the `pandas.datetime` submodule. " + filler
    assert len(text) > 1500
    head = " ".join(text.split())[:600]
    assert "pandas.datetime" not in head  # the old head-clip would have lost it
    out = snippet(text, symbol_terms(["pandas.datetime"]), 600)
    assert "pandas.datetime" in out and len(out) <= 640
    assert snippet("short text", ["x"], 600) == "short text"
    assert snippet(text, ["nomatch"], 600) == head[:599] + "\u2026"


def test_symbol_terms_prefers_full_names_then_parts():
    from trf.nodes.diff import symbol_terms

    t = symbol_terms(["pandas.read_csv(squeeze)", "pandas.DataFrame.append"])
    assert t[:2] == ["pandas.read_csv", "pandas.DataFrame.append"]
    assert "squeeze" in t and "append" in t and "read_csv" in t


def test_ambiguous_callee_is_attributed_to_all_matching_verdicts_not_dropped():
    from trf.nodes.rewrite import match_verdicts

    vs = {
        "pandas.read_csv(squeeze)": DiffVerdict(
            symbol="pandas.read_csv(squeeze)", verdict="signature_changed",
            evidence_chunk_ids=["a"], confidence=0.9),
        "pandas.read_csv(date_parser)": DiffVerdict(
            symbol="pandas.read_csv(date_parser)", verdict="signature_changed",
            evidence_chunk_ids=["b"], confidence=0.9),
        "pandas.DataFrame.append": DiffVerdict(
            symbol="pandas.DataFrame.append", verdict="removed",
            evidence_chunk_ids=["c"], confidence=0.9),
    }  # fmt: skip
    got = match_verdicts("pandas.read_csv", vs)
    assert sorted(v.symbol for v in got) == [
        "pandas.read_csv(date_parser)",
        "pandas.read_csv(squeeze)",
    ]
    assert [v.symbol for v in match_verdicts("pandas.DataFrame.append", vs)] == [
        "pandas.DataFrame.append"
    ]
    assert match_verdicts("pandas.unknown", vs) == []
    assert match_verdicts(None, vs) == []


def test_rewrite_keeps_an_edit_reported_under_the_bare_callee():
    s = with_evidence(prepared())
    s = s.model_copy(
        update={
            "verdicts": [
                DiffVerdict(symbol="pandas.DataFrame.append", verdict="removed",
                            evidence_chunk_ids=["pandas:wn:1"], confidence=0.9),
                DiffVerdict(symbol="pandas.DataFrame.append(ignore_index)", verdict="signature_changed",
                            evidence_chunk_ids=["pandas:wn:1"], confidence=0.9),
            ]
        }
    )  # fmt: skip
    new = 'import pandas as pd\ndf = pd.DataFrame({"a": [1]})\ndf = pd.concat([df, pd.DataFrame([{"a": 2}])])\nprint(len(df))\n'
    llm = FakeLLM(
        RewriteOut(code=new, changes=[ChangeOut(symbol="df.append", description="concat")])
    )
    out = make_rewrite(llm, RETRIEVAL)(s)
    assert [b.source for b in out["blocks"] if b.id == "b2"] == [new]
    assert out["change_log"] and all(
        e.evidence_chunk_ids for e in out["change_log"] if e.kind == "api_change"
    )


def test_attribute_by_diff_links_an_edit_to_the_verdicts_it_touched():
    from trf.nodes.rewrite import attribute_by_diff

    def v(sym, cid):
        return DiffVerdict(symbol=sym, verdict="removed", evidence_chunk_ids=[cid], confidence=0.9)

    mine = [
        v("sqlalchemy.engine.Engine.execute", "c1"),
        v("sqlalchemy.MetaData(bind)", "c2"),
        v("sqlalchemy.select", "c3"),
    ]
    old = "meta = MetaData(bind=engine)\nrows = engine.execute(text)\nprint(rows)\n"
    new = (
        "meta = MetaData()\nwith engine.connect() as c:\n    rows = c.execute(text)\nprint(rows)\n"
    )
    got = attribute_by_diff(old, new, mine)
    assert sorted(x.symbol for x in got) == [
        "sqlalchemy.MetaData(bind)",
        "sqlalchemy.engine.Engine.execute",
    ]  # `select` was not in the changed lines
    assert attribute_by_diff(old, old, mine) == []  # no edit, nothing to attribute


def test_rewrite_keeps_a_correct_edit_when_the_model_names_no_symbol():
    s = with_evidence(prepared())
    s = s.model_copy(
        update={
            "verdicts": [
                DiffVerdict(
                    symbol="pandas.DataFrame.append",
                    verdict="removed",
                    evidence_chunk_ids=["pandas:wn:1"],
                    confidence=0.9,
                ),
                DiffVerdict(
                    symbol="pandas.DataFrame.append(ignore_index)",
                    verdict="signature_changed",
                    evidence_chunk_ids=["pandas:wn:1"],
                    confidence=0.9,
                ),
            ]
        }
    )
    new = (
        'import pandas as pd\ndf = pd.DataFrame({"a": [1]})\n'
        'df = pd.concat([df, pd.DataFrame([{"a": 2}])])\nprint(len(df))\n'
    )
    llm = FakeLLM(RewriteOut(code=new, changes=[]))  # the model reported nothing
    out = make_rewrite(llm, RETRIEVAL)(s)
    assert [b.source for b in out["blocks"] if b.id == "b2"] == [new]
    entries = [e for e in out["change_log"] if e.block_id == "b2" and e.kind == "api_change"]
    assert entries and all(e.evidence_chunk_ids for e in entries)

"""Prose updates: which sentences are candidates, which edits are allowed, how they are applied."""

from test_diff_rewrite_assemble import FakeLLM
from trf.config import PipelineFlags
from trf.nodes.assemble import assemble
from trf.nodes.prose import (
    ProseEditOut,
    ProseOut,
    candidates,
    changed_symbols,
    make_prose,
    sentences,
    validate_edit,
)
from trf.state import ChangeLogEntry, CodeBlock, DiffVerdict, GraphState, Segment

ON = PipelineFlags(update_prose=True)

PROSE = (
    "# Adding rows\n\n"
    "This tutorial uses pandas 1.3.5. The `append` method returns a new frame. "
    "Run the loop 1.5 times.\n\n"
    "`groupby` splits the data into groups. Each step takes about 2.1 seconds.\n"
)


def state(prose=PROSE, edited=True):
    code = CodeBlock.new("b2", "df = df.append(row)\n")
    if edited:
        code = code.model_copy(update={"source": "df = pd.concat([df, row])\n"})
    return GraphState(
        segments=[
            Segment(kind="prose", text=prose),
            Segment(kind="code", text=code.source, block_id="b2", fence_open="```python\n",
                    fence_close="```\n"),
        ],
        blocks=[code],
        libraries=["pandas"],
        old_versions={"pandas": "1.3.5"},
        target_versions={"pandas": "2.3.3"},
        change_log=[
            ChangeLogEntry(
                block_id="b2", symbol="pandas.DataFrame.append", description="append -> concat",
                evidence_chunk_ids=["c1"],
            )
        ],
        verdicts=[
            DiffVerdict(symbol="pandas.DataFrame.append", verdict="removed",
                        replacement="pandas.concat", evidence_chunk_ids=["c1"], confidence=0.9)
        ],
    )  # fmt: skip


def test_sentences_skip_headings_front_matter_and_tables():
    text = "---\ntitle: x\n---\n# Head\n\nOne. Two three.\n| a | b |\n\nFour.\n"
    assert sentences(text) == ["One.", "Two three.", "Four."]


def test_only_sentences_tied_to_a_change_or_a_version_are_candidates():
    c = candidates(state())
    assert [(s, why) for _, s, why in c] == [
        ("This tutorial uses pandas 1.3.5.", "version"),
        ("The `append` method returns a new frame.", "append"),
    ]  # "1.5 times", "2.1 seconds" and `groupby` are left out


def test_an_unedited_block_makes_its_symbol_a_non_candidate():
    assert changed_symbols(state(edited=False)) == {}
    assert [w for _, _, w in candidates(state(edited=False))] == ["version"]


def test_validate_edit_rules():
    syms = {"append": "x"}
    ok = validate_edit(
        "This uses pandas 1.3.5.", "This uses pandas 2.3.3.", "version", {"2", "3"}, syms
    )
    assert ok is None
    old = "The `append` method returns a new frame."
    assert validate_edit(old, old, "append", set(), syms) == "no change"
    assert (
        validate_edit(old, "The `concat` function returns a new frame.", "append", set(), syms)
        is None
    )
    assert "rewritten" in validate_edit(
        old, "Completely different sentence about plotting.", "append", set(), syms
    )
    assert "length" in validate_edit(old, old * 3, "append", set(), syms)
    assert "backticks" in validate_edit(
        old, "The `concat method returns a new frame.", "append", set(), syms
    )
    assert "tied" in validate_edit(
        "Plotting uses 3 axes.", "Plotting uses 4 axes.", "", set(), syms
    )
    assert "numbers" in validate_edit(
        old, "The `append` method returns a new 5 frame.", "append", set(), syms
    )
    link = "See [docs](https://a.org/x) for `append`."
    assert (
        validate_edit(link, "See [docs](https://b.org/x) for `concat`.", "append", set(), syms)
        == "links changed"
    )


def run(edits, st=None):
    llm = FakeLLM(ProseOut(edits=edits))
    return make_prose(llm, ON)(st or state()), llm


def test_valid_edits_are_applied_by_exact_replacement_and_recorded():
    out, llm = run([
        ProseEditOut(id=0, new="This tutorial uses pandas 2.3.3.", reason="target version", linked="version"),
        ProseEditOut(id=1, new="The `concat` function returns a new frame.", reason="append removed", linked="append"),
    ])  # fmt: skip
    text = out["segments"][0].text
    assert "pandas 2.3.3" in text and "The `concat` function returns a new frame." in text
    assert "Run the loop 1.5 times." in text and "`groupby` splits the data into groups." in text
    assert text.startswith("# Adding rows\n\n")  # structure intact
    assert [e.linked for e in out["prose_edits"]] == ["version", "append"]
    assert "pandas 1.3.5" in llm.prompts[0] and "[0] This tutorial" in llm.prompts[0]
    assert "1.5 times" not in llm.prompts[0]  # distractors are never shown


def test_bad_edits_are_dropped_with_a_warning_and_good_ones_still_apply():
    out, _ = run([
        ProseEditOut(id=0, new="This tutorial uses pandas 2.3.3.", linked="version"),
        ProseEditOut(id=1, new="The `append` method returns a new 9 frame.", linked="append"),
        ProseEditOut(id=7, new="ghost sentence", linked="append"),
    ])  # fmt: skip
    assert [e.new for e in out["prose_edits"]] == ["This tutorial uses pandas 2.3.3."]
    assert any("introduces numbers" in w for w in out["warnings"])


def test_nothing_is_applied_when_every_edit_is_rejected():
    out, _ = run(
        [ProseEditOut(id=1, new="Nothing like the original at all, truly.", linked="append")]
    )
    assert "segments" not in out and "prose_edits" not in out


def test_no_candidates_means_no_llm_call_and_flag_off_is_a_no_op():
    plain = state(prose="Only text about `groupby`.\n")
    llm = FakeLLM()
    assert make_prose(llm, ON)(plain) == {} and llm.prompts == []
    assert make_prose(llm, PipelineFlags())(state()) == {}


def test_assemble_renders_edited_prose_and_lists_the_edits():
    out, _ = run(
        [
            ProseEditOut(
                id=0, new="This tutorial uses pandas 2.3.3.", reason="target", linked="version"
            )
        ]
    )
    st = state().model_copy(update=out)
    done = assemble(st)
    assert "This tutorial uses pandas 2.3.3." in done["output_md"]
    assert "This tutorial uses pandas 1.3.5." not in done["output_md"]
    assert "## Prose changes" in done["change_log_md"]
    assert "pandas 1.3.5._ -> _This tutorial uses pandas 2.3.3." in done["change_log_md"]


def test_graph_runs_repair_and_prose_stages_without_flags_unchanged():
    from trf.graph import build_graph

    md = "# t\n\n```python\nprint(1)\n```\n"
    nodes = {"sandbox": lambda s: {"blocks": [b.model_copy(update={"status": "passing"}) for b in s.blocks],
                                    "loop_count": s.loop_count + 1}}  # fmt: skip
    out = build_graph(nodes).invoke(GraphState(tutorial_md=md), {"recursion_limit": 50})
    assert out["output_md"] == md and out["prose_edits"] == []


def test_removed_code_names_make_their_sentences_candidates():
    from trf.nodes.prose import added_digits, removed_names

    code = CodeBlock.new("b2", "engine.table_names()\nx = 1\n").model_copy(
        update={"source": "inspect(engine).get_table_names()\nx = 1\nstyle('v0_8')\n"}
    )
    st = GraphState(
        segments=[
            Segment(kind="prose", text="Check it with `engine.table_names()`. Add 1 and 2.\n")
        ],
        blocks=[code],
        libraries=["sqlalchemy"],
    )
    assert set(removed_names(st)) == {"table_names"}  # `engine`, `x` are still in the new code
    assert added_digits(st) == {"0", "8"}
    assert [(s, w) for _, s, w in candidates(st)] == [
        ("Check it with `engine.table_names()`.", "table_names")
    ]


def test_a_version_sentence_is_allowed_to_change_even_if_the_model_names_another_link():
    old = "Pick the `seaborn` style, which Matplotlib 3.4 ships built in."
    new = "Pick the `seaborn-v0_8` style, which Matplotlib 3.11 ships built in."
    assert validate_edit(old, new, "version", {"3", "11", "0", "8"}, {}) is None
    assert "numbers" in validate_edit(old, new, "version", {"3", "11"}, {})

from trf.eval.metrics import (
    diff_accuracy,
    minimal_edit,
    pass_counts,
    tutorial_passes,
    unresolved_count,
)
from trf.state import CodeBlock, DiffVerdict, GraphState, Symbol


def blk(id, status, lang="python"):
    return CodeBlock.new(id, "x", lang).model_copy(update={"status": status})


def test_pass_counts_ignore_non_python_and_tutorial_level():
    s = GraphState(
        blocks=[blk("b1", "pending", "bash"), blk("b2", "passing"), blk("b3", "failing")]
    )
    assert pass_counts(s) == (1, 2) and not tutorial_passes(s)
    ok = GraphState(blocks=[blk("b1", "pending", "bash"), blk("b2", "passing")])
    assert tutorial_passes(ok)
    assert not tutorial_passes(GraphState())  # nothing ran is not a pass


def test_unresolved_count():
    s = GraphState(
        blocks=[blk("b1", "unresolved"), blk("b2", "passing"), blk("b3", "pending", "bash")]
    )
    assert unresolved_count(s) == (1, 2)


def test_diff_accuracy_defaults_to_unchanged():
    syms = [
        Symbol(name="pandas.DataFrame.append", library="pandas"),
        Symbol(name="pandas.read_csv", library="pandas"),
        Symbol(name="pandas.datetime", library="pandas"),
        Symbol(name="pip install pandas", library="pandas", kind="cli"),
    ]
    verdicts = [
        DiffVerdict(
            symbol="pandas.DataFrame.append",
            verdict="renamed",
            evidence_chunk_ids=["c"],
            confidence=1,
        ),
        DiffVerdict(
            symbol="pandas.read_csv", verdict="removed", evidence_chunk_ids=["c"], confidence=1
        ),
    ]
    s = GraphState(symbols=syms, verdicts=verdicts, libraries=["pandas"])
    expected = {"pandas.DataFrame.append": "removed", "pandas.datetime": "removed"}
    m = diff_accuracy(s, expected)
    assert m == {
        "symbols": 3,  # CLI line excluded
        "correct": 0,  # append wrong verdict, read_csv false alarm, datetime missed
        "changed_expected": 2,
        "changed_predicted": 2,
        "changed_hit": 1,  # append flagged as changed (verdict differs), datetime missed
        "verdict_exact": 0,
    }


def test_minimal_edit_counts_only_unrelated_changes():
    orig = "# T\n\n```python\na = 1\nb = 2\nc = 3\n```\n"
    ref = "# T\n\n```python\na = 1\nb = 20\nc = 3\n```\n"
    same_as_ref = minimal_edit(orig, ref, ref)
    assert same_as_ref["unrelated_lines"] == 0 and same_as_ref["prose_changed_segments"] == 0
    noisy = "# T\n\n```python\na = 10\nb = 20\nc = 30\n```\n"
    m = minimal_edit(orig, noisy, ref)
    assert m["unrelated_lines"] == 2 and m["code_lines"] == 3 and round(m["ratio"], 2) == 0.67
    untouched = minimal_edit(orig, orig, ref)
    assert untouched["unrelated_lines"] == 0  # missing a fix is not 'unrelated churn'


def test_minimal_edit_detects_prose_changes_and_structure_mismatch():
    orig = "# T\n\n```python\na = 1\n```\n"
    prose_edit = "# Title changed\n\n```python\na = 1\n```\n"
    assert minimal_edit(orig, prose_edit, orig)["prose_changed_segments"] == 1
    assert minimal_edit(orig, orig + "\n```python\nb\n```\n", orig) is None


def test_minimal_edit_insertion_counts_as_a_change():
    orig = "```python\na = 1\nb = 2\n```\n"
    inserted = "```python\na = 1\nimport os\nb = 2\n```\n"
    assert minimal_edit(orig, inserted, orig)["unrelated_lines"] == 1


def test_prose_score_counts_correct_and_unneeded_edits():
    from types import SimpleNamespace as E

    from trf.eval.metrics import prose_score

    expected = [
        {"old": "Uses pandas 1.3.5.", "new": "Uses pandas 2.3.3."},
        {"old": "`append` adds a row.", "new": "`concat` adds a row."},
    ]
    got = [
        E(old="Uses pandas 1.3.5.", new="Uses pandas 2.3.3."),
        E(old="`append` adds a row.", new="Something quite different here."),
        E(old="`groupby` splits data.", new="`groupby` splits the data."),
    ]
    assert prose_score(expected, got) == {"expected": 2, "applied": 3, "correct": 1, "unneeded": 1}


def test_prose_score_matches_whole_sentences_against_listed_clauses():
    from types import SimpleNamespace as E

    from trf.eval.metrics import prose_score

    expected = [
        {
            "old": "The tests were run on Matplotlib 3.4,",
            "new": "The tests were run on Matplotlib 3.11,",
        }
    ]
    got = [
        E(old="The tests were run on Matplotlib 3.4, and colours differ.",
          new="The tests were run on Matplotlib 3.11.2, and colours differ."),
        E(old="Plotting is fun.", new="Plotting is great."),
    ]  # fmt: skip
    assert prose_score(expected, got) == {"expected": 1, "applied": 2, "correct": 1, "unneeded": 1}

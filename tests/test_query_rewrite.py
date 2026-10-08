import pytest

from trf.nodes.query_rewrite import make_query_rewrite, symbols_from_error
from trf.state import CodeBlock, DiffVerdict, GraphState, RunResult, Symbol

PANDAS = ["pandas"]
PYDANTIC = ["pydantic"]


def names(error, source="", libs=PANDAS, known=()):
    return [n for n, _ in symbols_from_error(error, source, libs, list(known))]


@pytest.mark.parametrize(
    "error, libs, expected",
    [
        (
            "AttributeError: 'DataFrame' object has no attribute 'append'",
            PANDAS,
            ["pandas.DataFrame.append"],
        ),
        (
            "AttributeError: module 'pandas' has no attribute 'datetime'",
            PANDAS,
            ["pandas.datetime"],
        ),
        (
            "TypeError: read_csv() got an unexpected keyword argument 'squeeze'",
            PANDAS,
            ["pandas.read_csv(squeeze)"],
        ),
        (
            "AttributeError: 'User' object has no attribute 'dict'",
            PYDANTIC,
            ["pydantic.BaseModel.dict"],
        ),
        (
            "AttributeError: type object 'User' has no attribute 'parse_obj'",
            PYDANTIC,
            ["pydantic.BaseModel.parse_obj"],
        ),
        (
            "pydantic.errors.PydanticImportError: `BaseSettings` has been moved to the "
            "`pydantic-settings` package.",
            PYDANTIC,
            ["pydantic.BaseSettings"],
        ),
        (
            "ImportError: cannot import name 'parse_obj_as' from 'pydantic'",
            PYDANTIC,
            ["pydantic.parse_obj_as"],
        ),
    ],
)
def test_error_patterns(error, libs, expected):
    assert names(error, libs=libs) == expected


def test_removed_keyword_is_attached_to_its_call():
    err = "pydantic.errors.PydanticUserError: `regex` is removed. use `pattern` instead"
    src = "class U(BaseModel):\n    name: str = Field(..., regex='^a')\n"
    assert names(err, src, PYDANTIC) == ["pydantic.Field(regex)"]


def test_dunder_root_maps_to_basemodel():
    err = "PydanticUserError: `__root__` models are no longer supported in v2"
    assert names(err, libs=PYDANTIC) == ["pydantic.BaseModel.__root__"]


def test_known_symbol_is_reused_not_reinvented():
    known = [Symbol(name="pandas.DataFrame.to_csv(line_terminator)", library="pandas")]
    err = "TypeError: NDFrame.to_csv() got an unexpected keyword argument 'line_terminator'"
    assert names(err, known=known, libs=PANDAS) == ["pandas.DataFrame.to_csv(line_terminator)"]


def test_unrelated_error_names_nothing():
    assert names("ZeroDivisionError: division by zero") == []
    assert names("NameError: name 'df' is not defined") == []


def test_ambiguous_library_is_not_guessed():
    # two libraries, a bare name the source does not tie to either -> no symbol, no guess
    assert names("TypeError: foo() got an unexpected keyword argument 'x'", "foo(x=1)",
                 ["pandas", "pydantic"]) == []  # fmt: skip


def _state(error, source="df.append(row)\n", traceback=None):
    b = CodeBlock.new("b1", source).model_copy(update={"status": "failing", "last_error": error})
    results = [RunResult(block_id="b1", exit_code=1, traceback=traceback)] if traceback else []
    return GraphState(blocks=[b], libraries=["pandas"], run_results=results,
                      old_versions={"pandas": "1.3.5"})  # fmt: skip


def test_node_adds_fresh_symbols_and_maps_block():
    out = make_query_rewrite()(
        _state("AttributeError: 'DataFrame' object has no attribute 'append'")
    )
    assert out["error_symbols"] == {"b1": ["pandas.DataFrame.append"]}
    assert out["fresh_symbols"] == ["pandas.DataFrame.append"]
    (sym,) = out["symbols"]
    assert (
        sym.library == "pandas" and sym.old_version == "1.3.5" and sym.first_seen_in_block == "b1"
    )


def test_node_uses_full_traceback_when_present():
    tb = "Traceback (most recent call last):\n  ...\nAttributeError: 'Series' object has no attribute 'iteritems'"
    out = make_query_rewrite()(_state("AttributeError: ...", traceback=tb))
    assert out["error_symbols"] == {"b1": ["pandas.Series.iteritems"]}


def test_node_known_symbol_without_verdict_is_fresh_but_not_duplicated():
    s = _state("AttributeError: 'DataFrame' object has no attribute 'append'")
    s = s.model_copy(update={"symbols": [Symbol(name="pandas.DataFrame.append", library="pandas")]})
    out = make_query_rewrite()(s)
    assert out["error_symbols"] == {"b1": ["pandas.DataFrame.append"]}
    assert out["fresh_symbols"] == ["pandas.DataFrame.append"]  # diff had cleared it; re-judge
    assert len(out["symbols"]) == 1


def test_node_symbol_with_verdict_is_not_fresh():
    s = _state("AttributeError: 'DataFrame' object has no attribute 'append'")
    s = s.model_copy(
        update={
            "symbols": [Symbol(name="pandas.DataFrame.append", library="pandas")],
            "verdicts": [
                DiffVerdict(
                    symbol="pandas.DataFrame.append",
                    verdict="removed",
                    evidence_chunk_ids=["c1"],
                    confidence=0.9,
                )
            ],
        }
    )
    out = make_query_rewrite()(s)
    assert out["error_symbols"] == {"b1": ["pandas.DataFrame.append"]}
    assert out["fresh_symbols"] == []


def test_node_warns_and_skips_unexplained_error():
    out = make_query_rewrite()(_state("ZeroDivisionError: division by zero"))
    assert out["error_symbols"] == {}
    assert any("no library symbol identified for b1" in w for w in out["warnings"])


def test_node_ignores_passing_and_non_python_blocks():
    s = GraphState(
        blocks=[
            CodeBlock.new("ok", "x").model_copy(update={"status": "passing"}),
            CodeBlock.new("sh", "pip install x", lang="bash").model_copy(
                update={"status": "failing", "last_error": "boom"}
            ),
        ],
        libraries=["pandas"],
    )
    out = make_query_rewrite()(s)
    assert out["error_symbols"] == {} and out["warnings"] == []


def test_removed_module_attribute_maps_to_the_symbol_actually_used():
    known = [Symbol(name="pandas.np.mean", library="pandas")]
    err = "AttributeError: module 'pandas' has no attribute 'np'"
    assert names(err, known=known) == ["pandas.np", "pandas.np.mean"]
    assert names(err) == ["pandas.np"]  # nothing more specific known


def test_last_resort_match_only_looks_at_the_error_message_not_the_source_echo():
    known = [Symbol(name="pandas.Series.astype", library="pandas")]
    tb = "Traceback (most recent call last):\n    df['p'] = df['p'].astype(float)\nValueError: could not convert string to float: '$2.50'"
    assert names(tb, known=known) == []
    assert names("ValueError: bad astype call", known=known) == ["pandas.Series.astype"]


def test_downstream_cascade_is_not_warned_about_when_the_root_block_is_handled():
    root = CodeBlock.new("b2", "df.append(r)\n").model_copy(
        update={
            "status": "failing",
            "last_error": "AttributeError: 'DataFrame' object has no attribute 'append'",
        }
    )
    cascade = CodeBlock.new("b3", "print(df)\n").model_copy(
        update={
            "status": "failing",
            "last_error": "NameError: name 'df' is not defined [after earlier failure in b2; may be a downstream effect]",
        }
    )
    out = make_query_rewrite()(GraphState(blocks=[root, cascade], libraries=["pandas"]))
    assert out["error_symbols"] == {"b2": ["pandas.DataFrame.append"]}
    assert out["warnings"] == []
    # with nothing else explained, the same cascade IS reported
    alone = make_query_rewrite()(GraphState(blocks=[cascade], libraries=["pandas"]))
    assert any("b3" in w for w in alone["warnings"])


def test_block_broken_by_its_own_edit_is_retried_under_the_same_verdicts():
    from trf.state import ChangeLogEntry

    b = CodeBlock.new("b2", 'pd.read_csv(f, engine="pyarrow")\n').model_copy(
        update={
            "source": 'pd.read_csv(f, engine="pyarrow")\n',
            "original_source": "pd.read_csv(f)\n",
            "status": "failing",
            "last_error": "ImportError: Missing optional dependency 'pyarrow'.",
        }
    )
    entry = ChangeLogEntry(
        block_id="b2", symbol="pandas.read_csv", description="x", evidence_chunk_ids=["c1"]
    )
    s = GraphState(blocks=[b], libraries=["pandas"], change_log=[entry])
    out = make_query_rewrite()(s)
    assert out["error_symbols"] == {"b2": ["pandas.read_csv"]}
    assert out["fresh_symbols"] == []  # nothing new to retrieve; only the rewrite is retried
    assert any("retrying b2" in w for w in out["warnings"])


def test_bad_argument_value_is_traced_to_the_call_that_received_it():
    err = (
        "OSError: 'seaborn-whitegrid' is not a valid package style, path of style file, "
        "URL of style file, or library style name (library styles are listed in `style.available`)"
    )
    src = "import matplotlib.pyplot as plt\nplt.style.use('seaborn-whitegrid')\nplt.plot([1])\n"
    used = {"matplotlib.pyplot.style.use", "matplotlib.pyplot.plot"}
    got = symbols_from_error(err, src, ["matplotlib"], [], used)
    assert [n for n, _ in got] == ["matplotlib.pyplot.style.use"]
    assert symbols_from_error(err, src, ["matplotlib"], []) == []  # needs the block's used symbols
    assert symbols_from_error("ValueError: 'zzz' is bad", src, ["matplotlib"], [], used) == []


def test_node_uses_the_block_s_own_symbols_for_bad_values():
    b = CodeBlock.new(
        "b1", "import matplotlib.pyplot as plt\nplt.style.use('seaborn-whitegrid')\n"
    ).model_copy(
        update={
            "status": "failing",
            "last_error": "OSError: 'seaborn-whitegrid' is not a valid style",
        }
    )
    out = make_query_rewrite()(GraphState(blocks=[b], libraries=["matplotlib"]))
    assert out["error_symbols"] == {"b1": ["matplotlib.pyplot.style.use"]}

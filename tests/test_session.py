"""`>>>` interactive sessions as first-class tutorial code."""

from trf.nodes.assemble import assemble
from trf.nodes.ingest import ingest
from trf.session import is_session, parse_session, plain_fence, session_code
from trf.state import GraphState

SESSION = """>>> import numpy as np
>>> a = np.arange(6).reshape(2, 3)
>>> a
array([[0, 1, 2],
       [3, 4, 5]])
>>> for i in range(2):
...     print(i)
...
0
1
>>> a.sum()
15
"""

MD = f"# NumPy\n\nSome text.\n\n```pycon\n{SESSION}```\n\nMore text.\n"


def test_parse_session_statements_outputs_and_continuations():
    stmts = parse_session(SESSION)
    assert [s.code for s in stmts] == [
        ["import numpy as np"],
        ["a = np.arange(6).reshape(2, 3)"],
        ["a"],
        ["for i in range(2):", "    print(i)", ""],
        ["a.sum()"],
    ]
    assert stmts[2].output == ["array([[0, 1, 2],", "       [3, 4, 5]])"]
    assert stmts[3].output == ["0", "1"] and stmts[4].output == ["15"]
    assert session_code(stmts).startswith("import numpy as np\na = np.arange")


def test_is_session_and_non_sessions():
    assert is_session("\n>>> x = 1\n") and not is_session("x = 1\n>>> y")
    assert parse_session("prose first\n>>> x = 1\n") is None
    assert parse_session("x = 1\n") is None


def test_ingest_turns_a_session_into_an_executable_python_block():
    s = GraphState(tutorial_md=MD)
    blocks = ingest(s)["blocks"]
    (b,) = blocks
    assert b.lang == "python" and b.session
    assert b.source == session_code(b.session) and ">>>" not in b.source
    assert "array(" not in b.source  # output lines are not code
    # shell fences and ordinary python fences are untouched
    md2 = "```bash\n>>> not python\n```\n\n```python\nx = 1\n```\n"
    langs = [(x.lang, x.session is None) for x in ingest(GraphState(tutorial_md=md2))["blocks"]]
    assert langs == [("bash", True), ("python", True)]


def prepared(md=MD):
    s = GraphState(tutorial_md=md)
    return s.model_copy(update=ingest(s))


def test_an_unedited_session_is_written_back_byte_for_byte():
    s = prepared()
    assert assemble(s)["output_md"] == MD


def with_source(s, new_source):
    blocks = [b.model_copy(update={"source": new_source, "status": "passing"}) for b in s.blocks]
    return s.model_copy(update={"blocks": blocks})


def test_an_edited_session_stays_a_session_and_keeps_outputs_of_untouched_statements():
    s = prepared()
    new = s.blocks[0].source.replace("a.sum()", "int(a.sum())")
    out = assemble(with_source(s, new))["output_md"]
    assert "```pycon" in out
    assert ">>> int(a.sum())\n" in out
    assert ">>> a.sum()" not in out
    assert "15\n```" not in out  # the edited statement's output is dropped, not left stale
    assert ">>> a\narray([[0, 1, 2],\n       [3, 4, 5]])\n" in out  # untouched: output kept
    assert ">>> for i in range(2):\n...     print(i)\n...\n0\n1\n" in out


def test_an_edit_that_adds_lines_renders_continuation_prompts():
    s = prepared()
    new = s.blocks[0].source.replace(
        "a.sum()", "with open('x') as f:\n    f.read()\ntotal = a.sum()"
    )
    out = assemble(with_source(s, new))["output_md"]
    assert ">>> with open('x') as f:\n...     f.read()\n>>> total = a.sum()\n" in out


def test_code_that_does_not_parse_falls_back_to_a_plain_python_block():
    s = prepared()
    out = assemble(with_source(s, "x = (\n"))["output_md"]
    assert (
        "```python\nx = (\n```" in out and ">>>" not in out.split("More text")[0].split("Some")[1]
    )


def test_plain_fence_only_swaps_session_languages():
    assert plain_fence("```pycon\n") == "```python\n"
    assert plain_fence("```ipython3 title\n") == "```python title\n"
    assert plain_fence("```python\n") == "```python\n"
    assert plain_fence("```\n") == "```\n"
    assert plain_fence("~~~doctest\r\n") == "~~~python\r\n"

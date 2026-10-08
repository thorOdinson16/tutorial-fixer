import pytest

from trf.nodes.assemble import build_change_log
from trf.nodes.diagnose import diagnose
from trf.state import CodeBlock, GraphState


@pytest.mark.parametrize(
    "error, kind, needle",
    [
        ("NameError: name 'List' is not defined. Did you mean: 'list'?", "missing_import",
         "from typing import List"),
        ("NameError: name 'frame' is not defined", "undefined_name", "frame"),
        ("NameError: name 'df' is not defined [after earlier failure in b2; may be a downstream "
         "effect]", "cascade", "b2"),
        ("ModuleNotFoundError: No module named 'app.schemas'", "local_module", "app.schemas"),
        ("ModuleNotFoundError: No module named 'foobarbaz'", "missing_dependency", "foobarbaz"),
        ("ImportError: Missing optional dependency 'pyarrow'.  Use pip", "missing_dependency",
         "pyarrow"),
        ("FileNotFoundError: [Errno 2] No such file or directory: 'sales.csv'", "data_file",
         "sales.csv"),
        ("urllib.error.URLError: <urlopen error [Errno -3] Temporary failure>", "network",
         "network"),
        ("timed out after 60s", "timeout", "time limit"),
        ("stdout missing expected text: '3'", "output_mismatch", "prints"),
    ],
)  # fmt: skip
def test_diagnose_reads_common_tutorial_defects(error, kind, needle):
    d = diagnose(error)
    assert d is not None and d.kind == kind and needle in d.text


def test_real_rot_and_empty_errors_get_no_diagnosis():
    assert diagnose("AttributeError: 'DataFrame' object has no attribute 'append'") is None
    assert diagnose("TypeError: read_csv() got an unexpected keyword argument 'squeeze'") is None
    assert diagnose(None) is None and diagnose("") is None


def test_change_log_explains_unresolved_blocks():
    b = CodeBlock.new("b2", "x: List[int] = []").model_copy(
        update={"status": "unresolved", "last_error": "NameError: name 'List' is not defined"}
    )
    md = build_change_log(GraphState(blocks=[b]), [b])
    assert "- **b2**: NameError: name 'List' is not defined" in md
    assert "Likely cause: uses `List` but no earlier code imports it" in md

"""Static pre-repair: forgotten imports are added before symbol extraction."""

from trf.config import PipelineFlags
from trf.nodes.prerepair import make_prerepair, missing_imports
from trf.state import CodeBlock, GraphState

ON = PipelineFlags(repair_defects=True)


def blocks(*sources, lang="python"):
    return [CodeBlock.new(f"b{i + 2}", s, lang=lang) for i, s in enumerate(sources)]


def test_a_name_used_but_never_imported_gets_its_import_in_the_first_block_using_it():
    bs = blocks("x = 1\n", "df = pd.DataFrame({'a': [x]})\n", "print(pd.__version__)\n")
    assert missing_imports(bs) == {"b3": ["import pandas as pd"]}  # once, in the first user


def test_imports_assignments_and_definitions_count_as_defined():
    bs = blocks(
        "import numpy as np\nnp.zeros(1)\n",
        "def f(pd):\n    return pd.x\n",
        "plt = object()\nplt.show\n",
        "from typing import List\nx: List[int] = []\n",
        "for math in [1]:\n    math.real\n",
    )
    assert missing_imports(bs) == {}


def test_typing_names_and_unparseable_blocks():
    bs = blocks("def f(a: List[int]) -> Optional[str]:\n    return None\n", "this is not python(")
    assert missing_imports(bs) == {"b2": ["from typing import List", "from typing import Optional"]}


def test_magics_and_non_python_blocks_are_handled():
    bs = blocks("%matplotlib inline\nplt.plot([1])\n") + blocks("pip install pandas", lang="bash")
    assert missing_imports(bs) == {"b2": ["import matplotlib.pyplot as plt"]}


def test_node_edits_blocks_logs_defect_fixes_and_is_off_by_default():
    s = GraphState(blocks=blocks("x = np.arange(3)\n"))
    assert make_prerepair(PipelineFlags())(s) == {}
    out = make_prerepair(ON)(s)
    assert out["blocks"][0].source == "import numpy as np\nx = np.arange(3)\n"
    assert out["blocks"][0].original_source == "x = np.arange(3)\n"
    (e,) = out["change_log"]
    assert e.kind == "defect_fix" and "never imported" in e.error and e.block_id == "b2"
    assert make_prerepair(ON)(GraphState(blocks=blocks("print(1)\n"))) == {}

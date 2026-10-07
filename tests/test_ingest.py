from datetime import date

import pytest

from trf.nodes.ingest import (
    detect_lang,
    extract_pins,
    infer_date,
    ingest,
    render_segments,
    split_segments,
)
from trf.state import GraphState

MD = """---
date: 2021-06-14
---
# T

```bash
pip install pydantic==1.10.13 "pandas<2" -q
```

text

```python
import pandas as pd
print(1)
```
~~~py
x = 1
~~~
"""


def test_roundtrip_exact():
    assert render_segments(split_segments(MD)) == MD


@pytest.mark.parametrize(
    "md",
    ["", "no code\n", "```python\nunclosed\n", "a\r\n```py\r\nx=1\r\n```\r\nb", "```\n```\n"],
)
def test_roundtrip_edge_cases(md):
    assert render_segments(split_segments(md)) == md


def test_blocks_and_langs():
    out = ingest(GraphState(tutorial_md=MD))
    assert [(b.id, b.lang) for b in out["blocks"]] == [
        ("b1", "bash"),
        ("b2", "python"),
        ("b3", "python"),
    ]
    assert out["blocks"][1].source == "import pandas as pd\nprint(1)\n"
    assert out["blocks"][1].original_source == out["blocks"][1].source


def test_unclosed_fence_is_prose():
    out = ingest(GraphState(tutorial_md="```python\nx = 1\n"))
    assert out["blocks"] == []


def test_detect_lang():
    assert detect_lang("Python", "") == "python"
    assert detect_lang("{.python}", "") == "python"
    assert detect_lang("pycon", "") == "pycon"
    assert detect_lang("", ">>> 1 + 1\n2\n") == "pycon"
    assert detect_lang("", "import os\n") == "python"
    assert detect_lang("", "just words\n") == "text"
    assert detect_lang("json", "{}") == "json"


def test_infer_date():
    assert infer_date("date: 2021-06-14\n") == date(2021, 6, 14)
    assert infer_date("Posted on March 2, 2022 by me") == date(2022, 3, 2)
    assert infer_date("nothing here") is None


def test_pins():
    pins = extract_pins(
        'pip install pydantic==1.10.13 "pandas<2" -q numpy\n'
        "$ pip3 install --upgrade Scikit_Learn>=1.0,<1.3\n"
        "requests==2.25.1\n"
        "x >= 5\n"
    )
    assert pins == {
        "pydantic": "==1.10.13",
        "pandas": "<2",
        "scikit-learn": ">=1.0,<1.3",
        "requests": "==2.25.1",
    }


def test_ingest_infers_pins_and_date():
    out = ingest(GraphState(tutorial_md=MD))
    assert out["tutorial_date"] == date(2021, 6, 14)
    assert out["pins"]["pydantic"] == "==1.10.13"

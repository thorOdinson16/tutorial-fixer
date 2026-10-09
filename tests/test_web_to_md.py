import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import web_to_md  # noqa: E402

SESSION = """In [1]: df = pd.DataFrame(
   ...:     {"a": [1, 2]}
   ...: )

In [2]: df.sum()
Out[2]:
a    3
dtype: int64
"""


def test_ipython_session_becomes_plain_code():
    out = web_to_md.fence(SESSION)
    assert out.startswith("```python\n")
    assert 'df = pd.DataFrame(\n    {"a": [1, 2]}\n)' in out
    assert "df.sum()" in out
    assert "Out[2]" not in out and "dtype" not in out


def test_plain_text_is_not_an_ipython_session():
    assert web_to_md.ipython_code("SELECT a FROM t") is None


def test_scipy_pins():
    pins = dict(web_to_md.PIN_HINTS)
    assert pins[r"scipy-1\.10\.0"] == "scipy==1.10.1"

from trf.nodes.extract import analyze_block, extract, strip_magics
from trf.nodes.ingest import ingest
from trf.state import CodeBlock, GraphState

LIBS = {"pydantic", "pandas"}


def names(src):
    syms, _ = analyze_block("b1", src, LIBS)
    return {s.name: s for s in syms}


def test_pydantic_v1_surface():
    got = names(
        """
from pydantic import BaseModel, Field, validator

class A(BaseModel):
    zip: str = Field(..., regex="x")

    @validator("zip")
    def v(cls, x):
        return x

    class Config:
        orm_mode = True

u = A.parse_obj({})
u.dict()
u.json()
print(u.zip)
"""
    )
    for n in [
        "pydantic.BaseModel",
        "pydantic.Field",
        "pydantic.validator",
        "pydantic.Field(regex)",
        "pydantic.BaseModel.Config",
        "pydantic.BaseModel.Config.orm_mode",
        "pydantic.BaseModel.parse_obj",
        "pydantic.BaseModel.dict",
        "pydantic.BaseModel.json",
    ]:
        assert n in got, n
    assert got["pydantic.BaseModel.Config.orm_mode"].kind == "config_key"
    assert got["pydantic.Field(regex)"].kind == "argument"
    assert got["pydantic.BaseModel.parse_obj"].kind == "method"
    assert "pydantic.BaseModel.zip" not in got  # field access is not a library symbol


def test_pandas_methods_and_aliases():
    got = names(
        """
import pandas as pd
import io
df = pd.read_csv(io.StringIO(""), parse_dates=["d"])
df = df.append({}, ignore_index=True)
for k, v in df.groupby("r")["u"].sum().iteritems():
    pass
x = pd.datetime(2020, 1, 1)
df.to_csv("f", line_terminator="\\n")
pd.api.types.is_numeric_dtype(df)
"""
    )
    for n in [
        "pandas.read_csv",
        "pandas.read_csv(parse_dates)",
        "pandas.DataFrame.append",
        "pandas.DataFrame.append(ignore_index)",
        "pandas.datetime",
        "pandas.DataFrame.to_csv(line_terminator)",
        "pandas.api.types.is_numeric_dtype",
    ]:
        assert n in got, n
    assert "pandas.api" not in got and "pandas.api.types" not in got
    assert "pandas.DataFrame.iteritems" in got  # chained receiver keeps the DataFrame type


def test_unknown_libs_ignored_and_imports_collected():
    syms, imports = analyze_block("b1", "import numpy as np\nnp.array([1])\n", LIBS)
    assert syms == [] and imports == {"numpy"}


def test_syntax_error_falls_back_to_imports():
    syms, imports = analyze_block("b1", "import requests\nprint(", LIBS)
    assert syms == [] and imports == {"requests"}


def test_magics_stripped():
    assert strip_magics("!pip install x\nimport pandas\n%matplotlib inline") == (
        "pass\nimport pandas\npass"
    )
    src = "!pip install pandas\nimport pandas as pd\npd.read_csv('x')\n"
    syms, _ = analyze_block("b1", src, LIBS)
    assert any(s.name == "pandas.read_csv" for s in syms)


def test_extract_node_end_to_end():
    md = (
        "```bash\npip install pandas==1.3.5\n```\n"
        "```python\nimport pandas as pd\nimport numpy as np\n"
        "from sklearn.linear_model import X\nimport os\ndf = pd.DataFrame()\n```\n"
    )
    s = GraphState(tutorial_md=md)
    s = s.model_copy(update=ingest(s))
    out = extract(s)
    assert out["libraries"] == ["pandas"]
    assert out["extra_packages"] == ["numpy", "scikit-learn"]
    kinds = {x.name: x.kind for x in out["symbols"]}
    assert kinds["pip install pandas==1.3.5"] == "cli"
    assert kinds["pandas.DataFrame"] == "class"


def test_non_python_blocks_not_analyzed():
    s = GraphState(blocks=[CodeBlock.new("b1", "import pandas\n", lang="json")])
    assert extract(s)["symbols"] == []

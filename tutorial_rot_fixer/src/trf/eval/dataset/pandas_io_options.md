---
title: Reading and writing CSV with pandas
date: 2022-03-22
---

# Reading and writing CSV with pandas

Options for reading awkward CSV files and writing tidy ones.

```bash
pip install pandas==1.4.4
```

Skip lines that have too many fields:

```python
import io
import pandas as pd

messy = io.StringIO("""name,score
ann,10
bo,12,extra
cy,9
""")
df = pd.read_csv(messy, error_bad_lines=False, warn_bad_lines=False)
print(df["name"].tolist())
```

Files without a header get prefixed column names:

```python
nohdr = io.StringIO("1,2\n3,4\n")
grid = pd.read_csv(nohdr, header=None, prefix="col")
print(list(grid.columns))
```

Write with a fixed line terminator:

```python
out = io.StringIO()
df.to_csv(out, index=False, line_terminator="\n")
print(repr(out.getvalue()))
```

Force a column type on the way in:

```python
typed = pd.read_csv(io.StringIO("id,flag\n1,yes\n2,no\n"), dtype={"id": "int64"})
print(typed.dtypes.astype(str).to_dict())
```

JSON records:

```python
js = io.StringIO('[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]')
print(pd.read_json(js)["a"].tolist())
```

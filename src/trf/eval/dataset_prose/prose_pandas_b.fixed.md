---
title: Reading CSV, checking columns and grouping in pandas
date: 2022-04-02
---

# Reading CSV, checking columns and grouping in pandas

Options for reading awkward CSV files, walking columns and grouping. This tutorial uses pandas 2.3.3.

```bash
pip install pandas==2.3.3
```

Skip the lines that have too many fields. Setting `on_bad_lines` to `skip` drops the rows that have too many fields.

```python
import io
import pandas as pd
messy = io.StringIO("""name,score
ann,10
bo,12,extra
cy,9
""")
df = pd.read_csv(messy, on_bad_lines="skip")
print(df["name"].tolist())
```

Walk the columns of a frame. The frame below has 2 columns. `items` yields each column name with its values.

```python
df2 = pd.DataFrame({"name": ["ann"], "score": [10]})
print([name for name, _ in df2.items()])
```

Index labels can be floats. In pandas 2.0 and later, a plain `Index` holds float labels, since `Float64Index` was removed.

```python
idx = pd.Index([1.5, 2.5], dtype="float64")
print(idx.dtype)
```

Average a frame per group. `groupby` splits the rows into groups before it averages them. `mean` needs `numeric_only=True` to skip the text column.

```python
g = pd.DataFrame({"k": ["a", "a", "b"], "v": [1, 3, 5], "label": ["x", "y", "z"]})
print(g.groupby("k").mean(numeric_only=True).columns.tolist())
```

Write with a fixed line terminator. Pass `index=False` so the row numbers stay out of the file. `lineterminator` sets the line ending that `to_csv` writes. Each column keeps its own type, as step 1.5 shows.

```python
import io
import pandas as pd
small = pd.DataFrame({"a": [1, 2]})
out = io.StringIO()
small.to_csv(out, index=False, lineterminator="\n")
print(repr(out.getvalue()))
```

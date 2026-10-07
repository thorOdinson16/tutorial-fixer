---
title: Combining tables with pandas
date: 2021-12-01
---

# Combining tables with pandas

Merging, stacking and reshaping small tables.

```bash
pip install pandas==2.3.3
```

Join two tables on a key:

```python
import pandas as pd

left = pd.DataFrame({"k": [1, 2, 3], "a": ["x", "y", "z"]})
right = pd.DataFrame({"k": [2, 3, 4], "b": [20, 30, 40]})
print(left.merge(right, on="k", how="inner")["k"].tolist())
```

Build a table row by row:

```python
rows = []
for i in range(3):
    rows.append({"k": i, "a": str(i)})
frames = pd.DataFrame(rows, columns=["k", "a"])
print(len(frames), frames["a"].tolist())
```

Extend a series:

```python
s = pd.concat([pd.Series([1, 2]), pd.Series([3])], ignore_index=True)
print(s.tolist())
```

Reshape long data to wide:

```python
long = pd.DataFrame({"day": ["mon", "mon", "tue", "tue"], "kind": ["a", "b", "a", "b"], "n": [1, 2, 3, 4]})
wide = long.pivot(index="day", columns="kind", values="n")
print(wide.loc["tue"].tolist())
```

A quick mean via the bundled numpy:

```python
import numpy as np

print(np.mean([1, 2, 3]))
```

Drop duplicate keys, keeping the last:

```python
dups = pd.DataFrame({"k": [1, 1, 2], "v": [5, 6, 7]})
print(dups.drop_duplicates("k", keep="last")["v"].tolist())
```

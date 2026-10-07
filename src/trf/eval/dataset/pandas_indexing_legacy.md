---
title: Selecting and relabelling data
date: 2022-07-04
---

# Selecting and relabelling data

Label-based selection, relabelling and a few index checks.

```bash
pip install pandas==1.5.3
```

A small table:

```python
import pandas as pd

df = pd.DataFrame(
    {"city": ["Oslo", "Rome", "Lima", "Cairo"], "temp": [3, 18, 20, 25], "pop": [0.7, 2.8, 9.7, 9.5]}
)
print(df.shape)
```

Pick one value per row/column pair:

```python
rows = [0, 2, 3]
print(df.lookup(rows, ["temp", "temp", "pop"]).tolist())
```

Relabel the rows in place:

```python
df.set_axis(["a", "b", "c", "d"], axis=0, inplace=True)
print(df.index.tolist())
```

Are the labels and values sorted?

```python
print(df.index.is_monotonic, df["temp"].is_monotonic)
```

Boolean selection and the label of the maximum:

```python
temps = df["temp"]
print(temps[temps > 10].index.tolist(), temps.idxmax())
```

Summing over one level of a MultiIndex:

```python
s = pd.Series([1, 2, 3, 4], index=pd.MultiIndex.from_tuples([("x", 1), ("x", 2), ("y", 1), ("y", 2)]))
print(s.sum(level=0).to_dict())
```

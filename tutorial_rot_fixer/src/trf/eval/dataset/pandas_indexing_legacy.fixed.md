---
title: Selecting and relabelling data
date: 2022-07-04
---

# Selecting and relabelling data

Label-based selection, relabelling and a few index checks.

```bash
pip install pandas==2.3.3
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
print([df.at[r, c].item() for r, c in zip(rows, ["temp", "temp", "pop"])])
```

Relabel the rows in place:

```python
df = df.set_axis(["a", "b", "c", "d"], axis=0)
print(df.index.tolist())
```

Are the labels and values sorted?

```python
print(df.index.is_monotonic_increasing, df["temp"].is_monotonic_increasing)
```

Boolean selection and the label of the maximum:

```python
temps = df["temp"]
print(temps[temps > 10].index.tolist(), temps.idxmax())
```

Summing over one level of a MultiIndex:

```python
s = pd.Series([1, 2, 3, 4], index=pd.MultiIndex.from_tuples([("x", 1), ("x", 2), ("y", 1), ("y", 2)]))
print(s.groupby(level=0).sum().to_dict())
```

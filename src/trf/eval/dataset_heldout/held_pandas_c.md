---
title: Daily log bookkeeping with pandas
date: 2023-02-14
---

# Daily log bookkeeping with pandas

Notes on date ranges, bands, index helpers and week numbers for a small daily log.

```bash
pip install pandas==1.5.3
```

Ten days from New Year, half-open on the right:

```python
import pandas as pd
days = pd.date_range("2023-01-01", "2023-01-10", freq="D", closed="left")
print(len(days), days[-1].date())
```

Rolling sums over a small float series:

```python
totals = pd.Series([1.5, 2.5, 4.0]).rolling(2).sum().dropna()
print(totals.tolist())
```

Flag readings that fall inside a closed band:

```python
readings = pd.Series([3, 7, 12, 5], index=pd.date_range("2023-01-01", periods=4))
inside = readings.between(5, 12, inclusive=True)
print(inside.tolist())
```

Check the ordering of a float-valued index:

```python
idx = pd.Float64Index([1.0, 2.5, 4.0])
print(idx.is_monotonic_increasing)
```

ISO week numbers and an int64 view of a count column:

```python
stamps = pd.Series(pd.to_datetime(["2023-01-02", "2023-01-09"]))
print(stamps.dt.weekofyear.tolist())
print(pd.Series([1, 2]).view("int64").tolist())
```

Normalise some labels:

```python
labels = pd.Series(["a", "b"]).str.upper()
print(labels.tolist())
```

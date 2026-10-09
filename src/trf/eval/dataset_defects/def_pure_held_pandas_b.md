---
title: Index lookups, bounds and CSV headers in pandas
date: 2022-11-08
---

# Index lookups, bounds and CSV headers in pandas

Small lookups and filters on indexes, bounds and CSV headers.

```bash
pip install pandas==2.3.3
```

Look up a value by a float label:

```python
import io
prices = pd.Series([10, 20, 30], index=pd.Index([0.5, 1.0, 1.5], dtype="float64"))
print(prices.loc[1.0])
```

Find the slot a missing key would fall into:

```python
letters = pd.Index(["a", "c", "e"])
print(f"slot={letters.get_indexer(['d'], method='pad')[0]}")
```

Keep ages between two bounds, inclusive:

```python
ages = pd.Series([15, 18, 21, 40])
print(ages.between(18, 21, inclusive="both").tolist())
```

Read a CSV with a duplicated header name:

```python
raw = io.StringIO("id,id\n1,2\n")
frame = pd.read_csv(raw)
print(frame.columns.tolist())
```

Total the best score per team:

```python
totals = pd.DataFrame({"team": ["x", "y", "x"], "pts": [3, 5, 4]}).groupby("team")["pts"].max()
print(totals.to_dict())
```

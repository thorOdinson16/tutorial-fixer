---
title: Adding rows, counting values and date ranges in pandas
date: 2021-08-14
---

# Adding rows, counting values and date ranges in pandas

A few small pandas idioms for adding rows, grouping, counting and building date ranges. This tutorial uses pandas 2.3.3.

```bash
pip install pandas==2.3.3
```

Add a row to a frame from a dict. As of pandas 2.0, `DataFrame.append` is gone, so use `pd.concat` to add a row. `concat` returns a new frame, so assign the result. Before the append the frame has 2 rows.

```python
import pandas as pd
df = pd.DataFrame({"name": ["ann", "bo"], "score": [10, 12]})
df = pd.concat([df, pd.DataFrame([{"name": "cy", "score": 9}])], ignore_index=True)
print(df["name"].tolist())
```

Walk a series one pair at a time. `items` yields index and value pairs.

```python
s = pd.Series([3, 1], index=["x", "y"])
print([(k, v) for k, v in s.items()])
```

Build a daily range that leaves out the end date. The `inclusive` argument of `date_range` controls which end is left out.

```python
days = pd.date_range("2021-01-01", "2021-01-04", freq="D", inclusive="left")
print(days.day.tolist())
```

Sum a column per group. `groupby` splits the rows into groups before it sums them.

```python
sales = pd.DataFrame({"shop": ["a", "b", "a"], "n": [1, 2, 3]})
print(sales.groupby("shop")["n"].sum().to_dict())
```

Count the values in a series. `value_counts` sorts the counts, most common first. Skip step 1.5 if you only need the counts.

```python
words = pd.Series(["a", "a", "b"], name="word")
print(words.value_counts().to_dict())
```

---
title: Counting values and working with dates in pandas
date: 2021-05-10
---

# Counting values and working with dates in pandas

A few everyday pandas idioms for counting, dating and weekly grouping.

```bash
pip install pandas==1.3.5
```

Count the values and look at the label on the index:

```python
words = pd.Series(["a", "a", "b"], name="word")
counts = words.value_counts()
print(counts.index.name)
```

Build a daily range, leaving out the end date:

```python
days = pd.date_range("2021-01-01", "2021-01-04", freq="D", closed="left")
print(days.day.tolist())
```

Find the week number of each date:

```python
weeks = pd.Series(pd.to_datetime(["2021-01-04", "2021-06-30"])).dt.weekofyear
print(weeks.tolist())
```

Attach a frequency to a timestamp:

```python
ts = pd.Timestamp("2021-01-01", freq="D")
print(ts.day_name())
```

Sort a column and renumber the rows:

```python
print(pd.Series([3, 1, 2]).sort_values().reset_index(drop=True).tolist())
```

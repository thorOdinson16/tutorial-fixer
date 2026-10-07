---
title: Working with daily sales as a time series
date: 2022-02-14
---

# Working with daily sales as a time series

Load daily sales, group them by week and month and filter by date.

```bash
pip install pandas==1.4.4
```

Read the data into a single time-indexed series:

```python
import io
import pandas as pd

raw = io.StringIO("""day,sales
2022-01-03,10
2022-01-04,12
2022-01-10,7
2022-01-11,9
2022-01-17,15
""")
sales = pd.read_csv(raw, parse_dates=["day"], index_col="day", squeeze=True)
print(type(sales).__name__, len(sales))
```

Week numbers:

```python
print(list(sales.index.week))
```

Weekly and monthly totals:

```python
weekly = sales.resample("W").sum()
print(weekly.tolist())
monthly = sales.resample("M").sum()
print(monthly.tolist())
```

Everything since a cutoff date:

```python
cutoff = pd.datetime(2022, 1, 10)
print(sales[sales.index >= cutoff].sum())
```

Dates in day-first format:

```python
raw2 = io.StringIO("d,v\n03/01/2022,1\n04/01/2022,2\n")
parsed = pd.read_csv(raw2, parse_dates=["d"], date_parser=lambda s: pd.to_datetime(s, format="%d/%m/%Y"))
print(parsed["d"].dt.month.tolist())
```

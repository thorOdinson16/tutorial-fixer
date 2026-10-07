---
title: Working with daily sales as a time series
date: 2022-02-14
---

# Working with daily sales as a time series

Load daily sales, group them by week and month and filter by date.

```bash
pip install pandas==2.3.3
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
sales = pd.read_csv(raw, parse_dates=["day"], index_col="day").squeeze("columns")
print(type(sales).__name__, len(sales))
```

Week numbers:

```python
print(sales.index.isocalendar().week.tolist())
```

Weekly and monthly totals:

```python
weekly = sales.resample("W").sum()
print(weekly.tolist())
monthly = sales.resample("ME").sum()
print(monthly.tolist())
```

Everything since a cutoff date:

```python
from datetime import datetime

cutoff = datetime(2022, 1, 10)
print(sales[sales.index >= cutoff].sum())
```

Dates in day-first format:

```python
raw2 = io.StringIO("d,v\n03/01/2022,1\n04/01/2022,2\n")
parsed = pd.read_csv(raw2)
parsed["d"] = pd.to_datetime(parsed["d"], format="%d/%m/%Y")
print(parsed["d"].dt.month.tolist())
```

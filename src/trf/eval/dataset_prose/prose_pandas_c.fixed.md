---
title: Summarizing sales with pandas
date: 2021-09-14
---

# Summarizing sales with pandas

The sales table below is small enough to type in by hand. This tutorial uses pandas 2.3, so the examples follow that release's API.

```bash
pip install pandas==2.3.3
```

The pin above is pandas 2.3.3, the release that this tutorial was checked against.

Group the rows by region and sum the units with `groupby`. `groupby` does not change the original table. Step 1.2 of the outline sorts the rows by region as well.

```python
import io
import pandas as pd
raw = io.StringIO("""region,units,price
north,3,2.5
south,5,1.0
north,2,2.0
south,4,1.5
""")
sales = pd.read_csv(raw)
totals = sales.groupby("region")["units"].sum()
print(sales.shape, totals.tolist())
```

Add a row with `pd.concat`, which returns a new table and leaves the first one alone. Each price is a float, shown to one decimal place.

```python
extra = pd.DataFrame({"region": ["west"], "units": [7], "price": [3.0]})
bigger = pd.concat([sales, extra], ignore_index=True)
print(len(bigger), int(bigger["units"].sum()))
```

To see every column, loop over the table with `items`, which yields a (name, column) pair for each column.

```python
print([name for name, _ in bigger.items()])
```

Read a one-column file as a Series with `read_csv(...)` followed by `.squeeze("columns")`. The function `read_csv` parses numbers automatically.

```python
one = pd.read_csv(io.StringIO("total\n12\n")).squeeze("columns")
print(type(one).__name__, int(one.iloc[0]))
```

Build the day stamps with `date_range(inclusive='left')`, which drops the end date.

```python
days = pd.date_range(start="2021-01-01", end="2021-01-04", freq="D", inclusive="left")
print(len(days), days[-1].date())
```

---
title: Quick sales analysis with pandas
date: 2022-03-02
---

# Quick sales analysis with pandas

A small walkthrough of loading, extending and summarising sales data.

```bash
pip install pandas==2.3.3
```

## Loading the data

We use an inline CSV so the example is self-contained.

```python
import io
import pandas as pd

csv = io.StringIO("""date,region,product,units,price
2022-01-03,North,Widget,10,2.5
2022-01-04,South,Gadget,4,10.0
2022-01-05,North,Gadget,7,10.0
2022-01-06,East,Widget,3,2.5
""")
df = pd.read_csv(csv, parse_dates=["date"])
print(df.shape)
```

## Adding a row

`DataFrame.append` returns a new frame with the row added:

```python
new_row = {"date": pd.Timestamp("2022-01-07"), "region": "South", "product": "Widget", "units": 8, "price": 2.5}
df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
print(len(df))
```

## Summarising

Average revenue per region. Non-numeric columns are simply ignored by `mean()`:

```python
df["revenue"] = df["units"] * df["price"]
summary = df.groupby("region").mean(numeric_only=True)
print(summary["revenue"].round(2).to_dict())
```

Total units per region:

```python
for region, total in df.groupby("region")["units"].sum().items():
    print(region, total)
```

## Filtering by date

```python
cutoff = pd.Timestamp("2022-01-05")
recent = df[df["date"] >= cutoff]
print(len(recent))
```

## Exporting

```python
out = io.StringIO()
df.to_csv(out, index=False, lineterminator="\n")
print(out.getvalue().splitlines()[0])
```

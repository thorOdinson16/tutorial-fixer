---
title: Cleaning a messy product table
date: 2022-05-12
---

# Cleaning a messy product table

A short tour of everyday cleaning steps on a small product table.

```bash
pip install pandas==1.5.3
```

Load the raw data:

```python
import io
import pandas as pd

raw = io.StringIO("""id,name,price,qty
1, Widget ,$2.50,4
2,gadget,$10.00,
3,GIZMO,"$1,250.50",2
""")
df = pd.read_csv(raw)
print(df.shape)
```

Normalise names and turn prices into numbers (strip the dollar sign and thousands separators):

```python
df["name"] = df["name"].str.strip().str.title()
df["price"] = df["price"].str.replace("[$,]", "").astype(float)
print(df["price"].tolist())
```

Missing quantities become zero:

```python
df["qty"] = df["qty"].fillna(0).astype(int)
print(df["qty"].tolist())
```

A numeric index and the mean absolute deviation of the prices:

```python
idx = pd.Int64Index([10, 20, 30])
print(idx.dtype, df["price"].mad().round(2))
```

Dropping a column:

```python
slim = df.drop("qty", 1)
print(list(slim.columns))
```

Checking ordering:

```python
print(df["id"].is_monotonic, df["id"].is_unique)
```

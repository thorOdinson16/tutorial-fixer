---
title: Grouping and aggregating player stats
date: 2021-10-20
---

# Grouping and aggregating player stats

Split-apply-combine on a small table of team statistics.

```bash
pip install pandas==1.3.5
```

The data and the size of each group:

```python
import io
import pandas as pd

raw = io.StringIO("""team,player,points,rebounds
red,ann,10,3
red,bo,14,5
blue,cy,8,9
blue,di,12,2
green,ed,5,1
""")
df = pd.read_csv(raw)
print(df.groupby("team").size().to_dict())
```

Average per team (text columns are simply left out):

```python
means = df.groupby("team").mean()
print(means["points"].to_dict())
```

Different aggregations per column:

```python
agg = df.groupby("team").agg({"points": "sum", "rebounds": "max"})
print(agg.loc["red"].tolist())
```

Sort every group internally; the result keeps the original row index:

```python
ranked = df.groupby("team").apply(lambda g: g.sort_values("points", ascending=False))
print(ranked.index.nlevels, ranked["player"].tolist())
```

Each player's share of the team total:

```python
df["share"] = df["points"] / df.groupby("team")["points"].transform("sum")
print(df["share"].round(2).tolist())
```

Summing everything, including the text column:

```python
print(df.groupby("team").sum().loc["red", "player"])
```

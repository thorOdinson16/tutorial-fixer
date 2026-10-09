---
title: Log axes, box plots and colormaps in matplotlib
date: 2020-12-02
---

# Log axes, box plots and colormaps in matplotlib

A few plotting helpers for log scales, box plots and colormap lookups.

```bash
pip install matplotlib==3.11.2
```

Create a headless figure and check its size:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(4, 3))
print(fig.get_size_inches().tolist())
```

Use a log x-axis and clip non-positive values:

```python
ax.set_xscale("log", nonpositive="clip")
print(ax.get_xscale())
```

Count a histogram of small integers:

```python
counts, edges, _ = ax.hist([1, 2, 2, 3, 3, 3], bins=3)
print(counts.tolist(), [round(float(e), 2) for e in edges])
```

Draw one box plot with a label:

```python
bp = ax.boxplot([1, 2, 3, 4], tick_labels=["sample"])
print(len(bp["boxes"]))
```

Look up how many colours the viridis map has:

```python
print(matplotlib.colormaps["viridis"].N)
```

Use a log y-axis with base ten:

```python
ax.set_yscale("log", base=10)
print(ax.get_yscale())
```

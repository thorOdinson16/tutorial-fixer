---
title: Styled line and bar plots with matplotlib
date: 2021-06-14
---

# Styled line and bar plots with matplotlib

Set a style, draw a line chart, add bars and tweak the grid and colours.

```bash
pip install matplotlib==3.11.2
```

Start with a headless backend and the pyplot interface:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
print(type(fig).__name__)
```

Pick a built-in style and draw a line with markers:

```python
plt.style.use("seaborn-v0_8")
fig, ax = plt.subplots(figsize=(6, 4))
ax.plot([1, 2, 3], [2, 4, 9], marker="o")
print(matplotlib.rcParams["axes.facecolor"])
```

Sample four colours from viridis:

```python
cmap = matplotlib.colormaps["viridis"].resampled(4)
print(matplotlib.colors.to_hex(cmap(0)), matplotlib.colors.to_hex(cmap(3)))
```

Turn on a dotted grid:

```python
ax.grid(visible=True, linestyle=":")
print(ax.xaxis.get_gridlines()[0].get_visible())
```

Add bars on the same axes:

```python
bars = ax.bar(["a", "b", "c"], [3, 5, 2], color="tab:orange")
print(len(bars), round(sum(b.get_height() for b in bars)))
```

Read the RGB buffer size of a small figure:

```python
import numpy as np
fig = plt.figure(figsize=(2, 1), dpi=100)
fig.canvas.draw()
print(np.asarray(fig.canvas.buffer_rgba())[:, :, :3].size)
```

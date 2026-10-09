---
title: Styled line and bar plots with Matplotlib
date: 2021-09-02
---

# Styled line and bar plots with Matplotlib

Set a style, draw a line chart, add bars, and tweak the grid and colours.

```bash
pip install matplotlib==3.11.2
```

This tutorial uses Matplotlib 3.11. Start with a headless backend and the pyplot interface. The figure is 6 by 4 inches in the later steps.

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
print(type(fig).__name__)
```

Pick the `seaborn-v0_8` style, which Matplotlib 3.11 ships built in, and draw a line with markers. `ax.plot` draws the line itself.

```python
plt.style.use("seaborn-v0_8")
fig, ax = plt.subplots(figsize=(6, 4))
ax.plot([1, 2, 3], [2, 4, 9], marker="o")
print(matplotlib.rcParams["axes.facecolor"])
```

Sample four colours from viridis with `matplotlib.colormaps`. The tests were run on Matplotlib 3.11, and colour timings differ by 1.5 times between machines, so compare the hex values instead.

```python
cmap = matplotlib.colormaps["viridis"].resampled(4)
print(matplotlib.colors.to_hex(cmap(0)), matplotlib.colors.to_hex(cmap(3)))
```

Turn on a dotted grid with `grid(visible=True)`, then check that the first gridline is visible.

```python
ax.grid(visible=True, linestyle=":")
print(ax.xaxis.get_gridlines()[0].get_visible())
```

Add bars on the same axes. Bar heights are read back with `get_height`.

```python
bars = ax.bar(["a", "b", "c"], [3, 5, 2], color="tab:orange")
print(len(bars), round(sum(b.get_height() for b in bars)))
```

Read the RGB buffer size with `buffer_rgba`, which copies the canvas into bytes. Step 2.1 of the checklist uses the same size.

```python
fig = plt.figure(figsize=(2, 1), dpi=100)
fig.canvas.draw()
print(len(bytes(fig.canvas.buffer_rgba())) // 4 * 3)
```

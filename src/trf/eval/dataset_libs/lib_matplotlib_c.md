---
title: Colormaps and figure buffers in matplotlib
date: 2020-04-18
---

# Colormaps and figure buffers in matplotlib

Register a custom colormap, look up colormaps and read back a rendered figure.

```bash
pip install matplotlib==3.2.2
```

Start a headless figure:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(6, 4))
print(fig.get_size_inches().tolist())
```

Look up how many colours the viridis map has:

```python
import matplotlib.cm as cm
print(cm.cmap_d["viridis"].N)
```

Register a two-colour map and read it back:

```python
from matplotlib.colors import ListedColormap
cm.register_cmap(name="rg", cmap=ListedColormap(["red", "green"]))
print(plt.get_cmap("rg").N)
```

Cycle line colours and draw one line:

```python
ax.set_prop_cycle("color", ["red", "blue"])
line, = ax.plot([1, 2], [3, 4])
print(line.get_color())
```

Measure the RGB byte size of the rendered figure:

```python
fig.canvas.draw()
print(len(fig.canvas.tostring_rgb()))
```

Read the colormap length back from the registry:

```python
print(plt.cm.get_cmap("rg").N)
```

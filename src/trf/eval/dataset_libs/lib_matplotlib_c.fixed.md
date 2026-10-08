---
title: Colormaps and figure buffers in matplotlib
date: 2020-04-18
---

# Colormaps and figure buffers in matplotlib

Register a custom colormap, look up colormaps and read back a rendered figure.

```bash
pip install matplotlib==3.11.2
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
print(matplotlib.colormaps["viridis"].N)
```

Register a two-colour map and read it back:

```python
from matplotlib.colors import ListedColormap
matplotlib.colormaps.register(ListedColormap(["red", "green"], name="rg"))
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
import numpy as np
fig.canvas.draw()
print(np.asarray(fig.canvas.buffer_rgba())[:, :, :3].size)
```

Read the colormap length back from the registry:

```python
print(plt.get_cmap("rg").N)
```

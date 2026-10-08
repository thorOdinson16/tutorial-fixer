---
title: Bounds, ranges and flags in NumPy
date: 2020-11-02
---

# Bounds, ranges and flags in NumPy

Small helpers for finding bounds, spreads and thresholds in sensor arrays.

```bash
pip install numpy==2.5.3
```

Find the lowest sample, starting from an infinite sentinel:

```python
import numpy as np
samples = np.array([4.0, 2.5, 7.0])
best = np.inf
for x in samples:
    best = min(best, x)
print(best)
```

Spread of each row, using the array method:

```python
import numpy as np
grid = np.array([[1, 5, 3], [2, 2, 9]])
print(np.ptp(grid, axis=1).tolist())
```

Pick the positions that pass a threshold:

```python
import numpy as np
values = np.array([0.2, 0.9, 0.5, 0.7])
print(np.where(values > 0.4)[0].tolist())
```

Check that every reading is positive:

```python
import numpy as np
readings = np.array([0.5, 1.5, 2.0])
print(np.all(readings > 0))
```

Add a small amount to a byte counter:

```python
import numpy as np
counter = np.uint8(200)
print(int(counter) + 100)
```

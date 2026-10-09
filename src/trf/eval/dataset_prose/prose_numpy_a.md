---
title: Quick array statistics with NumPy
date: 2021-06-14
---

# Quick array statistics with NumPy

Small aggregates over arrays, written the way many older notebooks do it.

```bash
pip install numpy==1.21.6
```

This tutorial uses NumPy 1.21. We start with a float array of random integers, cast with `np.float`. The array has shape (4, 3), so it holds twelve values.

```python
import numpy as np
np.random.seed(0)
data = np.random.randint(0, 100, size=(4, 3)).astype(np.float)
print(data.shape, data.dtype)
```

Column products come from `np.product`, which multiplies along an axis. `np.cumproduct` gives running products of a list. Expect about 1.5 percent noise in any timing you add later.

```python
import numpy as np
rows = np.array([[1.0, 2.0], [3.0, 4.0]])
print(np.product(rows, axis=0).tolist(), np.cumproduct([1, 2, 3]).tolist())
```

Missing readings are marked with `np.NaN`. `np.isnan` finds them, and `np.nansum` adds the rest.

```python
import numpy as np
readings = np.array([1.0, np.NaN, 3.0])
print(np.isnan(readings).tolist(), np.nansum(readings))
```

Column means were checked on NumPy 1.21. `np.mean` averages the values, and `np.round` trims them to two places; the rounding rule is covered in section 2.1.

```python
import numpy as np
np.random.seed(0)
data = np.random.randint(0, 100, size=(4, 3)).astype(float)
print(np.round(data.mean(axis=0), 2).tolist())
```

Sort scores stored as `np.float_` values and keep the order with `np.argsort`.

```python
import numpy as np
scores = np.array([3.5, 1.25, 2.0], dtype=np.float_)
order = np.argsort(scores)
print(order.tolist(), scores[order].tolist())
```

---
title: Summary statistics with NumPy aliases
date: 2021-06-14
---

# Summary statistics with NumPy aliases

Quick aggregates over small arrays, written the way many older notebooks do it.

```bash
pip install numpy==1.21.6
```

Make a float array of random integers:

```python
import numpy as np
np.random.seed(0)
data = np.random.randint(0, 100, size=(4, 3)).astype(np.float)
print(data.shape, data.dtype)
```

Column products of the first two rows:

```python
import numpy as np
rows = np.array([[1.0, 2.0], [3.0, 4.0]])
print(np.product(rows, axis=0).tolist(), np.cumproduct([1, 2, 3]).tolist())
```

Mark missing readings with NaN and sum the rest:

```python
import numpy as np
readings = np.array([1.0, np.NaN, 3.0])
print(np.isnan(readings).tolist(), np.nansum(readings))
```

Column means, rounded to two places:

```python
import numpy as np
np.random.seed(0)
data = np.random.randint(0, 100, size=(4, 3)).astype(float)
print(np.round(data.mean(axis=0), 2).tolist())
```

Sort scores and keep the order:

```python
import numpy as np
scores = np.array([3.5, 1.25, 2.0])
order = np.argsort(scores)
print(order.tolist(), scores[order].tolist())
```

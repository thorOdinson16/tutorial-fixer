---
title: Stats and linear algebra basics with SciPy
date: 2021-11-20
---

# Stats and linear algebra basics with SciPy

Solving small systems, a pseudo-inverse, a sparse solve and a few summary statistics.

```bash
pip install scipy==1.7.3
```

Solve a 2 by 2 linear system:

```python
import numpy as np
from scipy import linalg
A = np.array([[4.0, 1.0], [2.0, 3.0]])
print(np.round(linalg.solve(A, np.array([1.0, 2.0])), 4).tolist())
```

Pseudo-inverse of a 3 by 2 matrix:

```python
from scipy.linalg import pinv2
B = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
print(np.round(pinv2(B), 4).tolist())
```

Conjugate gradient on a small sparse system, with a loose tolerance:

```python
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import cg
S = csr_matrix(np.array([[4.0, 1.0], [1.0, 3.0]]))
x, info = cg(S, np.array([1.0, 2.0]), tol=1e-10)
print(info, np.round(x, 4).tolist())
```

Most common value in each column:

```python
from scipy.stats import mode
data = np.array([[1, 2, 2], [3, 3, 4], [3, 5, 2]])
m = mode(data, axis=0)
print(m.mode.tolist(), m.count.tolist())
```

Frequency table of a short sample:

```python
from scipy.stats import itemfreq
sample = np.array([1, 2, 2, 3, 3, 3])
print(np.asarray(itemfreq(sample), dtype=float).tolist())
```

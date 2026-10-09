---
title: Numerical integration and interpolation with SciPy
date: 2021-03-15
---

# Numerical integration and interpolation with SciPy

Quick integrals and a two-dimensional interpolation, written the way tutorials from the SciPy 1.5 era do it.

```bash
pip install scipy==1.18.1
```

Integrate y = x**2 on [0, 1] with Simpson's rule and the trapezoid rule:

```python
import numpy as np
from scipy.integrate import simpson, trapezoid
x = np.linspace(0.0, 1.0, 5)
y = x ** 2
print(round(simpson(y, x=x), 4), round(trapezoid(y, x=x), 4))
```

Running totals of the same integral, with the starting value set to zero:

```python
from scipy.integrate import cumulative_trapezoid
print(np.round(cumulative_trapezoid(y, x, initial=0.0), 4).tolist())
```

Adaptive quadrature of the same function, for comparison:

```python
from scipy.integrate import quad
value, error = quad(lambda t: t ** 2, 0.0, 1.0)
print(round(value, 4))
```

Bilinear interpolation of a grid that holds x + y:

```python
from scipy.interpolate import RegularGridInterpolator
xs = np.array([0.0, 1.0, 2.0])
ys = np.array([0.0, 1.0, 2.0])
grid = np.add.outer(ys, xs)
f = RegularGridInterpolator((ys, xs), grid)
print(round(float(f([[1.5, 0.5]])[0]), 4))
```

Root of t**2 - 2 between 0 and 2:

```python
from scipy.optimize import brentq
root = brentq(lambda t: t ** 2 - 2.0, 0.0, 2.0)
print(round(root, 4))
```

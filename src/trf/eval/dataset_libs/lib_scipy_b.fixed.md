---
title: Signal smoothing with SciPy windows
date: 2021-09-02
---

# Signal smoothing with SciPy windows

Window functions and filters for smoothing short signals, in the style of the SciPy 1.6 documentation examples.

```bash
pip install scipy==1.18.1
```

A five-point Hann window:

```python
import numpy as np
from scipy.signal.windows import hann
print(np.round(hann(5), 4).tolist())
```

A five-point Gaussian window with unit standard deviation:

```python
from scipy.signal.windows import gaussian
print(np.round(gaussian(5, std=1.0), 4).tolist())
```

Median filter over a noisy short series:

```python
from scipy.signal import medfilt
x = np.array([1.0, 9.0, 2.0, 8.0, 3.0, 7.0, 4.0])
print(medfilt(x, 3).tolist())
```

Gaussian smoothing of a ramp along one axis:

```python
from scipy.ndimage import gaussian_filter1d
ramp = np.arange(10.0)
out = gaussian_filter1d(ramp, sigma=1.0)
print(round(float(out[5]), 4))
```

Blackman window samples at the second and third positions:

```python
from scipy.signal.windows import blackman
print(round(float(blackman(5)[1]), 4), round(float(blackman(5)[2]), 4))
```

---
title: Legacy type aliases and array conversion
date: 2022-07-18
---

# Legacy type aliases and array conversion

Conversion helpers from an older data-cleaning script, kept for reference.

```bash
pip install numpy==1.23.5
```

Round a reading to two places with the old helper:

```python
import numpy as np
reading = 3.14159
print(np.round_(reading, 2))
```

Store a value as the legacy float type:

```python
import numpy as np
legacy = np.float_(2.5) * 4
print(legacy, type(legacy).__name__)
```

Evenly spaced sample points:

```python
import numpy as np
pts = np.linspace(0, 1, 5)
print(pts[1:4].tolist())
```

Convert integer labels to floats:

```python
import numpy as np
labels = [1, 2, 4]
print(np.asfarray(labels).tolist())
```

Wrap a list as a float array without copying:

```python
import numpy as np
raw = [1, 2, 3]
view = np.array(raw, dtype=float, copy=False)
print(view.tolist())
```

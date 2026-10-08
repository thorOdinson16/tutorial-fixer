---
title: Validated readings summarised with pandas
date: 2023-06-20
---

# Validated readings summarised with pandas

Readings are validated with Pydantic models, then summarised with pandas.

```bash
pip install pydantic==1.10.13 pandas==1.5.3
```

A reading with an optional unit:

```python
from typing import Optional
from pydantic import BaseModel

class Reading(BaseModel):
    sensor: str
    value: float
    unit: Optional[str]

rows = [Reading(sensor="a", value=1.5), Reading(sensor="b", value=2, unit="C")]
print(len(rows), rows[0].unit)
```

A band helper for the summary:

```python
def band(value):
    return "hot" if value > 30 else "ok"

print([band(v) for v in [12.0, 31.5, 30.0]])
```

An aliased sample record:

```python
from pydantic import BaseModel

class Sample(BaseModel):
    reading_id: int
    temp_c: float

    class Config:
        fields = {"reading_id": {"alias": "id"}}

sample = Sample(id=7, temp_c=21.5)
print(sample.reading_id, sample.temp_c)
```

Totals per sensor:

```python
import pandas as pd

rows = [Reading(sensor="a", value=1.5, unit="C"), Reading(sensor="b", value=2.0, unit="C"), Reading(sensor="a", value=4.0, unit="C")]
frame = pd.DataFrame([{"sensor": r.sensor, "value": r.value} for r in rows])
totals = frame.groupby("sensor", squeeze=True)["value"].sum()
print(totals.to_dict())
```

Position of the last reading at or below 3.0:

```python
by_value = frame.sort_values("value").set_index("value")
print(by_value.index.get_loc(3.0, method="pad"))
```

Mean reading:

```python
print(round(frame["value"].mean(), 2))
```

---
title: Validated readings summarised with pandas
date: 2023-06-20
---

# Validated readings summarised with pandas

Readings are validated with Pydantic models, then summarised with pandas.

```bash
pip install pandas==2.3.3 pydantic==2.13.5
```

A reading with an optional unit:

```python
from typing import Optional
from pydantic import BaseModel

class Reading(BaseModel):
    sensor: str
    value: float
    unit: Optional[str] = None

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
from pydantic import BaseModel, Field

class Sample(BaseModel):
    reading_id: int = Field(alias="id")
    temp_c: float

sample = Sample(id=7, temp_c=21.5)
print(sample.reading_id, sample.temp_c)
```

Totals per sensor:

```python

rows = [Reading(sensor="a", value=1.5, unit="C"), Reading(sensor="b", value=2.0, unit="C"), Reading(sensor="a", value=4.0, unit="C")]
frame = pd.DataFrame([{"sensor": r.sensor, "value": r.value} for r in rows])
totals = frame.groupby("sensor")["value"].sum()
print(totals.to_dict())
```

Position of the last reading at or below 3.0:

```python
by_value = frame.sort_values("value").set_index("value")
print(by_value.index.get_indexer([3.0], method="pad")[0])
```

Mean reading:

```python
print(round(frame["value"].mean(), 2))
```

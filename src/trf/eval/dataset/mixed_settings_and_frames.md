---
title: Validated rows into a DataFrame
date: 2022-09-20
---

# Validated rows into a DataFrame

We validate incoming records with Pydantic, then analyse them with pandas.

```bash
pip install pydantic==1.10.13 pandas==1.5.3
```

## App settings

```python
from pydantic import BaseSettings


class AppSettings(BaseSettings):
    app_name: str = "demo"
    max_rows: int = 3

    class Config:
        env_prefix = "DEMO_"


settings = AppSettings()
print(settings.app_name, settings.max_rows)
```

## A validated row

`low` must not exceed `high`, and the note is optional:

```python
from typing import Optional
from pydantic import BaseModel, root_validator


class Row(BaseModel):
    name: str
    low: float
    high: float
    note: Optional[str]

    @root_validator
    def check_range(cls, values):
        if values["low"] > values["high"]:
            raise ValueError("low must be <= high")
        return values
```

## Into pandas

```python
import pandas as pd

records = [{"name": "a", "low": 1, "high": 2}, {"name": "b", "low": 0.5, "high": 4}]
rows = [Row(**r) for r in records]
frame = pd.DataFrame([r.dict() for r in rows])
print(frame["high"].sum())
```

## Adding another record

```python
extra = Row.parse_obj({"name": "c", "low": 2, "high": 3})
frame = frame.append(extra.dict(), ignore_index=True)
print(frame.shape[0], settings.max_rows)
```

## Tidying up

```python
frame = frame.drop("note", 1)
print(list(frame.columns))
print(frame.sort_values("high", ascending=False).head(settings.max_rows)["name"].tolist())
```

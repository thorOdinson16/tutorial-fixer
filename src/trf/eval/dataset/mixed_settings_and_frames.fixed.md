---
title: Validated rows into a DataFrame
date: 2022-09-20
---

# Validated rows into a DataFrame

We validate incoming records with Pydantic, then analyse them with pandas.

```bash
pip install pydantic==2.13.5 pandas==2.3.3 pydantic-settings
```

## App settings

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEMO_")

    app_name: str = "demo"
    max_rows: int = 3


settings = AppSettings()
print(settings.app_name, settings.max_rows)
```

## A validated row

`low` must not exceed `high`, and the note is optional:

```python
from typing import Optional
from pydantic import BaseModel, model_validator


class Row(BaseModel):
    name: str
    low: float
    high: float
    note: Optional[str] = None

    @model_validator(mode="after")
    def check_range(self):
        if self.low > self.high:
            raise ValueError("low must be <= high")
        return self
```

## Into pandas

```python
import pandas as pd

records = [{"name": "a", "low": 1, "high": 2}, {"name": "b", "low": 0.5, "high": 4}]
rows = [Row(**r) for r in records]
frame = pd.DataFrame([r.model_dump() for r in rows])
print(frame["high"].sum())
```

## Adding another record

```python
extra = Row.model_validate({"name": "c", "low": 2, "high": 3})
frame = pd.concat([frame, pd.DataFrame([extra.model_dump()])], ignore_index=True)
print(frame.shape[0], settings.max_rows)
```

## Tidying up

```python
frame = frame.drop(columns="note")
print(list(frame.columns))
print(frame.sort_values("high", ascending=False).head(settings.max_rows)["name"].tolist())
```

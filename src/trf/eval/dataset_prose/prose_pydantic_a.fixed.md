---
title: Validating records with pydantic
date: 2021-11-20
---

# Validating records with pydantic

Small pydantic models for checking input records. This tutorial uses pydantic 2.14.0.

```bash
pip install pydantic==2.14.0
```

A code must be three capital letters. As of pydantic 2.0, `Field(pattern=...)` is the way to add a pattern check. The `pattern` argument does the check, so a bad code raises a ValidationError.

```python
from pydantic import BaseModel, Field
class Code(BaseModel):
    code: str = Field(..., pattern=r"^[A-Z]{3}$")
print(Code(code="ABC").code)
```

Item has a default for `qty`, so it can be left out. `model_dump` returns the fields as a plain dict.

```python
class Item(BaseModel):
    name: str
    qty: int = 1
print(Item(name="x").model_dump())
```

Step 1.5 shows the error text. `model_validator` with `mode="after"` runs after the fields are checked.

```python
from pydantic import BaseModel, model_validator
class Range(BaseModel):
    lo: int
    hi: int
    @model_validator(mode="after")
    def check(self):
        if self.lo > self.hi:
            raise ValueError("lo > hi")
        return self
print(Range(lo=1, hi=2).hi)
```

A custom root type subclasses `RootModel` with its type in brackets. `RootModel` declares the one value the model wraps.

```python
from typing import List
from pydantic import RootModel
class Tags(RootModel[List[str]]):
    pass
print(Tags(["a", "b"]).root)
```

The model below has 2 required fields. Its sum is checked in the last line.

```python
class Pair(BaseModel):
    a: int
    b: int
print(Pair(a=1, b=2).a + Pair(a=3, b=4).b)
```

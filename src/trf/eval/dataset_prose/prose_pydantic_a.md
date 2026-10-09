---
title: Validating records with pydantic
date: 2021-11-20
---

# Validating records with pydantic

Small pydantic models for checking input records. This tutorial uses pydantic 1.10.13.

```bash
pip install pydantic==1.10.13
```

A code must be three capital letters. As of pydantic 1.10, `Field(regex=...)` is the way to add a pattern check. The `regex` argument does the check, so a bad code raises a ValidationError.

```python
from pydantic import BaseModel, Field
class Code(BaseModel):
    code: str = Field(..., regex=r"^[A-Z]{3}$")
print(Code(code="ABC").code)
```

Item has a default for `qty`, so it can be left out. `dict` returns the fields as a plain dict.

```python
class Item(BaseModel):
    name: str
    qty: int = 1
print(Item(name="x").dict())
```

Step 1.5 shows the error text. `root_validator` runs after the fields are checked.

```python
from pydantic import BaseModel, root_validator
class Range(BaseModel):
    lo: int
    hi: int
    @root_validator
    def check(cls, values):
        if values["lo"] > values["hi"]:
            raise ValueError("lo > hi")
        return values
print(Range(lo=1, hi=2).hi)
```

A custom root type uses `__root__` in the class body. `__root__` declares the one value the model wraps.

```python
from typing import List
from pydantic import BaseModel
class Tags(BaseModel):
    __root__: List[str]
print(Tags(__root__=["a", "b"]).__root__)
```

The model below has 2 required fields. Its sum is checked in the last line.

```python
class Pair(BaseModel):
    a: int
    b: int
print(Pair(a=1, b=2).a + Pair(a=3, b=4).b)
```

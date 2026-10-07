---
title: Reading ORM objects and using dataclasses
date: 2022-08-30
---

# Reading ORM objects and using dataclasses

Pydantic can build models from arbitrary objects and validate plain dataclasses.

```bash
pip install pydantic==2.13.5
```

A stand-in ORM row and a response model that can read from it:

```python
from typing import Optional
from pydantic import BaseModel, ConfigDict


class UserRow:
    def __init__(self, id, name, email=None):
        self.id = id
        self.name = name
        self.email = email


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: Optional[str] = None
```

Build the response model from the row:

```python
user = UserOut.model_validate(UserRow(1, "Ann"))
print(user.id, user.name, user.email)
```

Converting a list of rows:

```python
rows = [UserRow(1, "Ann", "ann@example.com"), UserRow(2, "Bo")]
users = [UserOut.model_validate(r) for r in rows]
print([u.model_dump() for u in users])
```

Pydantic dataclasses validate and coerce their fields:

```python
from pydantic.dataclasses import dataclass


@dataclass
class Dim:
    w: int
    h: int

    def area(self):
        return self.w * self.h


d = Dim("3", "4")
print(d.area(), type(d.w).__name__)
```

Validators work on dataclasses too:

```python
from pydantic import field_validator


@dataclass
class Temp:
    celsius: float

    @field_validator("celsius")
    @classmethod
    def above_zero_k(cls, v):
        if v < -273.15:
            raise ValueError("below absolute zero")
        return v


print(Temp(20).celsius)
try:
    Temp(-300)
except ValueError:
    print("rejected")
```

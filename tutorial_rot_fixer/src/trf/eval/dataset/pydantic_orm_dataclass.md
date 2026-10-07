---
title: Reading ORM objects and using dataclasses
date: 2022-08-30
---

# Reading ORM objects and using dataclasses

Pydantic can build models from arbitrary objects and validate plain dataclasses.

```bash
pip install pydantic==1.10.13
```

A stand-in ORM row and a response model that can read from it:

```python
from typing import Optional
from pydantic import BaseModel


class UserRow:
    def __init__(self, id, name, email=None):
        self.id = id
        self.name = name
        self.email = email


class UserOut(BaseModel):
    id: int
    name: str
    email: Optional[str] = None

    class Config:
        orm_mode = True
```

Build the response model from the row:

```python
user = UserOut.from_orm(UserRow(1, "Ann"))
print(user.id, user.name, user.email)
```

Converting a list of rows:

```python
rows = [UserRow(1, "Ann", "ann@example.com"), UserRow(2, "Bo")]
users = [UserOut.from_orm(r) for r in rows]
print([u.dict() for u in users])
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
from pydantic import validator


@dataclass
class Temp:
    celsius: float

    @validator("celsius")
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

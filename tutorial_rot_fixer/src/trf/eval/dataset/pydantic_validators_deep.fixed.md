---
title: Custom validators in Pydantic
date: 2021-09-02
---

# Custom validators in Pydantic

Validators let you clean and check fields beyond what type hints can express.

```bash
pip install pydantic==2.13.5
```

Field validators: strip input before validation, check every list item, and enforce a minimum.

```python
from typing import List
from pydantic import BaseModel, field_validator


class Order(BaseModel):
    customer: str
    items: List[str]
    quantity: int = 1
    note: str = ""

    @field_validator("customer", mode="before")
    @classmethod
    def strip_customer(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("items")
    @classmethod
    def item_not_empty(cls, v):
        out = []
        for item in v:
            if not item:
                raise ValueError("empty item")
            out.append(item.lower())
        return out

    @field_validator("quantity")
    @classmethod
    def positive(cls, v):
        if v < 1:
            raise ValueError("quantity must be >= 1")
        return v
```

Valid input is cleaned on the way in:

```python
order = Order(customer="  Ada ", items=["Pen", "INK"], quantity=2)
print(order.customer, order.items, order.quantity)
```

Invalid input reports every failing field:

```python
from pydantic import ValidationError

try:
    Order(customer="Bob", items=["pad", ""], quantity=0)
except ValidationError as e:
    print(len(e.errors()))
    print(sorted({err["loc"][0] for err in e.errors()}))
```

A validator can look at fields validated before it through `values`:

```python
from datetime import date
from pydantic import BaseModel, ValidationInfo, field_validator


class Trip(BaseModel):
    start: date
    end: date

    @field_validator("end")
    @classmethod
    def end_after_start(cls, v, info: ValidationInfo):
        if "start" in info.data and v < info.data["start"]:
            raise ValueError("end before start")
        return v


print(Trip(start="2024-01-01", end="2024-01-05").end)
try:
    Trip(start="2024-02-01", end="2024-01-05")
except ValueError as e:
    print("rejected:", "end before start" in str(e))
```

Defaults are normally not validated; `always=True` forces it so we can compute a default:

```python
from pydantic import Field


class Greeting(BaseModel):
    name: str = "world"
    message: str = Field("", validate_default=True)

    @field_validator("message")
    @classmethod
    def build_message(cls, v, info: ValidationInfo):
        return v or f"hello {info.data['name']}"


print(Greeting().message)
```

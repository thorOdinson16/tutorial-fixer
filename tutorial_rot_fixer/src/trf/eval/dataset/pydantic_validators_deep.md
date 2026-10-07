---
title: Custom validators in Pydantic
date: 2021-09-02
---

# Custom validators in Pydantic

Validators let you clean and check fields beyond what type hints can express.

```bash
pip install pydantic==1.10.13
```

Field validators: strip input before validation, check every list item, and enforce a minimum.

```python
from typing import List
from pydantic import BaseModel, validator


class Order(BaseModel):
    customer: str
    items: List[str]
    quantity: int = 1
    note: str = ""

    @validator("customer", pre=True)
    def strip_customer(cls, v):
        return v.strip() if isinstance(v, str) else v

    @validator("items", each_item=True)
    def item_not_empty(cls, v):
        if not v:
            raise ValueError("empty item")
        return v.lower()

    @validator("quantity")
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
from pydantic import BaseModel, validator


class Trip(BaseModel):
    start: date
    end: date

    @validator("end")
    def end_after_start(cls, v, values):
        if "start" in values and v < values["start"]:
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
class Greeting(BaseModel):
    name: str = "world"
    message: str = ""

    @validator("message", always=True)
    def build_message(cls, v, values):
        return v or f"hello {values['name']}"


print(Greeting().message)
```

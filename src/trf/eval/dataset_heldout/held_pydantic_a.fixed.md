---
title: Shipments and loads with Pydantic
date: 2021-11-08
---

# Shipments and loads with Pydantic

Pydantic lets you describe a shipment with type hints and checks the data for you.
Install it first:

```bash
pip install pydantic==2.13.5
```

## A fixed kind

Every shipment carries a constant `kind` so consumers can tell record types apart.

```python
from typing import Literal
from pydantic import BaseModel


class Shipment(BaseModel):
    kind: Literal["shipment"] = "shipment"
    shipment_id: int


s = Shipment(shipment_id="7")
print(s.kind, s.shipment_id)
```

## Bounded pallet counts

A load holds between one and forty pallets. The pallet type is a constrained integer.

```python
from typing import Annotated
from pydantic import BaseModel, Field, ValidationError


class Load(BaseModel):
    pallets: Annotated[int, Field(ge=1, le=40)]


print(Load(pallets="12").pallets)
try:
    Load(pallets=50)
except ValidationError:
    print("too many pallets")
```

## Optional fields

A driver may have no phone number, so the phone field is optional and defaults to nothing.

```python
from typing import Optional
from pydantic import BaseModel


class Driver(BaseModel):
    name: str
    phone: Optional[str] = None


d = Driver(name="Ana")
print(d.name, d.phone)
```

## Vehicles with a trailer

A vehicle record has a plate and an optional trailer id that may be left out.

```python
from typing import Optional
from pydantic import BaseModel


class Vehicle(BaseModel):
    plate: str
    trailer_id: Optional[str] = None


v = Vehicle(plate="X1")
print(v.plate, v.trailer_id)
```

## Checking trip distance

A trip distance must not be negative. The validator names the field in its error message.

```python
from pydantic import BaseModel, ValidationError, ValidationInfo, field_validator


class Trip(BaseModel):
    km: int

    @field_validator("km")
    @classmethod
    def not_negative(cls, v, info: ValidationInfo):
        if v < 0:
            raise ValueError(f"{info.field_name} must be >= 0")
        return v


try:
    Trip(km=-5)
except ValidationError as e:
    print("rejected:", "km must be >= 0" in str(e))
```

## Routes

A route has a lane and a number of stops, which is coerced from text.

```python
from enum import Enum
from pydantic import BaseModel


class Lane(str, Enum):
    north = "N"
    south = "S"


class Route(BaseModel):
    lane: Lane
    stops: int = 1


r = Route(lane="N", stops="3")
print(r.lane.value, r.stops)
```

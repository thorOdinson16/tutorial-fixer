---
title: Scaling recipes and readings with Pydantic
date: 2022-02-03
---

# Scaling recipes and readings with Pydantic

Pydantic checks the shape of kitchen data for you, using plain type hints.
Install it first:

```bash
pip install pydantic==1.10.13
```

## Required fields

A recipe must have a title. Servings default to two.

```python
from pydantic import BaseModel, Required


class Recipe(BaseModel):
    title: str = Required
    servings: int = 2


r = Recipe(title="Soup")
print(r.title, r.servings)
```

## Temperature readings

A reading holds a Celsius temperature between minus fifty and sixty degrees.

```python
from pydantic import BaseModel, ConstrainedFloat, ValidationError


class Celsius(ConstrainedFloat):
    ge = -50.0
    le = 60.0


class Reading(BaseModel):
    temp: Celsius


print(Reading(temp="21.5").temp)
try:
    Reading(temp=99)
except ValidationError:
    print("too hot")
```

## Ingredients

An ingredient has a name of at least two letters, an optional note, and a weight in grams.

```python
from typing import Optional
from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    name: str = Field(..., min_length=2)
    note: Optional[str] = None
    grams: float = Field(0.0, ge=0)


i = Ingredient(name="Salt", grams="12.5")
print(i.name, i.note, i.grams)
```

## Short codes

A product code is a string of at most four characters.

```python
from pydantic import BaseModel, ConstrainedStr, ValidationError


class Code(ConstrainedStr):
    max_length = 4


class Tag(BaseModel):
    code: Code


print(Tag(code="SOUP").code)
try:
    Tag(code="TOOLONG")
except ValidationError:
    print("code too long")
```

## Batches

A batch lists recipe ids, and every id must appear only once.

```python
from typing import List
from pydantic import BaseModel, Field, ValidationError


class Batch(BaseModel):
    ids: List[int] = Field(..., unique_items=True)


print(Batch(ids=[3, 1, 2]).ids)
try:
    Batch(ids=[1, 1])
except ValidationError:
    print("duplicate ids")
```

## Units

Each measure uses a unit from a fixed list and an amount that is coerced from text.

```python
from enum import Enum
from pydantic import BaseModel


class Unit(str, Enum):
    g = "g"
    ml = "ml"


class Measure(BaseModel):
    unit: Unit = Unit.g
    amount: int = 1


m = Measure(amount="250")
print(m.unit.value, m.amount)
```

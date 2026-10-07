---
title: Aliases and model configuration in Pydantic
date: 2021-11-10
---

# Aliases and model configuration in Pydantic

Model configuration controls aliasing, whitespace handling, extra fields and mutability.

```bash
pip install pydantic==2.13.5
```

A product model with an aliased field and a few config options:

```python
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class Product(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="forbid",
        json_schema_extra={"example": {"SKU": "A1", "title": "Pen", "price": 1.5}},
    )

    sku: str = Field(..., alias="SKU")
    title: str
    price: float = Field(..., gt=0)
    tags: Optional[list] = None
```

You can populate by alias or by field name:

```python
p = Product(SKU="A1", title="  Pen  ", price=1.5)
q = Product(sku="B2", title="Ink", price=3)
print(p.sku, repr(p.title), q.sku)
```

Exporting, with the alias as the key:

```python
print(p.model_dump(by_alias=True))
print(p.model_dump_json(by_alias=True, exclude_none=True))
```

Unknown fields are rejected because of `extra = "forbid"`:

```python
from pydantic import ValidationError

try:
    Product(SKU="C3", title="Cap", price=2, color="red")
except ValidationError as e:
    print(e.errors()[0]["loc"])
```

The JSON schema reflects aliases and requirements:

```python
schema = Product.model_json_schema()
print(schema["title"], sorted(schema["properties"]))
print(schema["required"])
```

Models can be made immutable:

```python
class Point(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: int
    y: int


pt = Point(x=1, y=2)
try:
    pt.x = 5
except ValidationError:
    print("immutable")
```

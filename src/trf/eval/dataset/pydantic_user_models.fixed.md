---
title: Validating user data with Pydantic
date: 2021-06-14
---

# Validating user data with Pydantic

Pydantic lets you declare the shape of your data with type hints and validates it for you.
Install it first:

```bash
pip install pydantic==2.13.5
```

## Defining models

We model a user with a nested address. The zip code must be five digits and names must be
capitalized.

```python
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Address(BaseModel):
    street: str
    zip_code: str = Field(..., pattern=r"^\d{5}$")


class User(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    tags: List[str] = []
    address: Address

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    @field_validator("name")
    @classmethod
    def name_must_be_capitalized(cls, v):
        if not v[0].isupper():
            raise ValueError("name must be capitalized")
        return v
```

## Parsing data

`parse_obj` builds a model from a dict, coercing types (note that `"1"` becomes `1`):

```python
data = {"id": "1", "name": "Alice", "address": {"street": "1 Main St", "zip_code": "12345"}}
user = User.model_validate(data)
print(user.id, user.name)
```

## Exporting

```python
print(user.model_dump())
print(user.model_dump_json())
print(sorted(User.model_json_schema()["required"]))
```

## Handling errors

```python
from pydantic import ValidationError

try:
    User.model_validate({"id": "x", "name": "bob", "address": {"street": "s", "zip_code": "1"}})
except ValidationError as e:
    print(len(e.errors()))
```

## Copying and round-tripping

```python
user2 = user.model_copy(update={"name": "Bob"})
print(user2.name)
same = User.model_validate_json(user.model_dump_json())
print(same.id)
```

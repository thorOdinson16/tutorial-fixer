---
title: Constrained types and optional fields in Pydantic
date: 2022-04-05
---

# Constrained types and optional fields in Pydantic

Constrained types express string patterns, numeric bounds and list sizes right in the annotation.

```bash
pip install pydantic==2.13.5
```

A signup form with constrained fields:

```python
from typing import List
from pydantic import BaseModel, conint, constr, conlist


class Signup(BaseModel):
    username: constr(pattern=r"^[a-z0-9_]+$", min_length=3)
    age: conint(ge=13, le=120)
    interests: conlist(str, min_length=1, max_length=3)
```

Valid data is coerced:

```python
ok = Signup(username="ada_l", age="30", interests=["math"])
print(ok.username, ok.age, ok.interests)
```

Each violated constraint is reported against its field:

```python
from pydantic import ValidationError

try:
    Signup(username="Ad", age=5, interests=[])
except ValidationError as e:
    print(sorted({err["loc"][0] for err in e.errors()}))
```

URL fields and optional values:

```python
from typing import Optional
from pydantic import HttpUrl


class Link(BaseModel):
    url: HttpUrl
    label: Optional[str] = None


link = Link(url="https://example.com/docs")
print(link.url, link.url.host, link.label)
```

The URL behaves like a string:

```python
print(str(link.url).startswith("https"), str(link.url).endswith("/docs"))
```

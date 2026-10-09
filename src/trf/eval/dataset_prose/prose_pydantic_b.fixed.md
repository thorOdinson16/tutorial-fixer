---
title: Validating a server config with pydantic
date: 2022-01-18
---

# Validating a server config with pydantic

Pydantic checks data against type hints and converts values where it can. This tutorial uses pydantic 2.14, so the models below follow the 2.x API.

```bash
pip install pydantic==2.14.0
```

The pin above is pydantic 2.14.0, the version this tutorial was checked against.

Define a server model with a default port and an empty tag list. The string "9000" is converted to an integer port. Fields are listed in the order they are declared.

```python
from typing import List
from pydantic import BaseModel
class Server(BaseModel):
    host: str
    port: int = 8080
    tags: List[str] = []
s = Server(host="example.org", port="9000", tags=["a"])
print(s.port + 1, s.tags)
```

Call `.model_dump()` on the model to get its values as a plain dict. `List[str]` means a list of strings, so the tags print as a Python list.

```python
print(s.model_dump())
```

Use `Field(pattern=...)` to check the format of a slug, so only lowercase letters and dashes pass. Step 2.1 of the outline lists the default port.

```python
from pydantic import Field
class Tag(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z-]+$")
print(Tag(slug="ab-cd").slug)
```

In pydantic 2.14, a field annotated `Optional[str]` with no default is required. The printed value below is the default, not an error.

```python
from typing import Optional
class Note(BaseModel):
    title: str
    body: Optional[str] = None
print(Note(title="x").body)
```

A model with a single unnamed field uses `RootModel`, as in the next block. The value comes back as a plain list, not wrapped in an object.

```python
from typing import List
from pydantic import RootModel
class Tags(RootModel[List[str]]):
    pass
print(Tags(["a", "b"]).root)
```

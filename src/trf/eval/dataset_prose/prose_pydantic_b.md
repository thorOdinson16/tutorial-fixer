---
title: Validating a server config with pydantic
date: 2022-01-18
---

# Validating a server config with pydantic

Pydantic checks data against type hints and converts values where it can. This tutorial uses pydantic 1.10, so the models below follow the 1.x API.

```bash
pip install pydantic==1.10.13
```

The pin above is pydantic 1.10.13, the version this tutorial was checked against.

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

Call `.dict()` on the model to get its values as a plain dict. `List[str]` means a list of strings, so the tags print as a Python list.

```python
print(s.dict())
```

Use `Field(regex=...)` to check the format of a slug, so only lowercase letters and dashes pass. Step 2.1 of the outline lists the default port.

```python
from pydantic import Field
class Tag(BaseModel):
    slug: str = Field(..., regex=r"^[a-z-]+$")
print(Tag(slug="ab-cd").slug)
```

In pydantic 1.10, a field annotated `Optional[str]` with no default is not required. The printed value below is the default, not an error.

```python
from typing import Optional
class Note(BaseModel):
    title: str
    body: Optional[str]
print(Note(title="x").body)
```

A model with a single unnamed field uses `__root__`, as in the next block. The value comes back as a plain list, not wrapped in an object.

```python
from typing import List
class Tags(BaseModel):
    __root__: List[str]
print(Tags(__root__=["a", "b"]).__root__)
```

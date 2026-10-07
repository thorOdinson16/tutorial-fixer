---
title: Root models, generics and forward references
date: 2022-01-18
---

# Root models, generics and forward references

Beyond plain models: models whose value is a single container, generic models and self-references.

```bash
pip install pydantic==2.13.5
```

A custom root type wraps a single value:

```python
from typing import Dict, List
from pydantic import BaseModel, RootModel, TypeAdapter


class Scores(RootModel[Dict[str, int]]):
    pass


scores = Scores({"ann": 3, "bo": 5})
print(scores.root["bo"], len(scores.root))
```

Validate arbitrary types without defining a model:

```python
nums = TypeAdapter(List[int]).validate_python(["1", "2", 3])
print(nums, sum(nums))
```

A model can refer to itself:

```python
from typing import Optional


class Node(BaseModel):
    value: int
    next: Optional["Node"] = None


Node.model_rebuild()
chain = Node(value=1, next=Node(value=2))
print(chain.next.value)
```

Generic models parametrise a field type:

```python
from typing import Generic, TypeVar

T = TypeVar("T")


class Box(BaseModel, Generic[T]):
    item: T


print(Box[int](item="7").item)
```

Copying and constructing without validation:

```python
n = Node(value=5)
m = n.model_copy(update={"value": 6})
print(n.value, m.value)
print(Node.model_construct(value=9).value)
```

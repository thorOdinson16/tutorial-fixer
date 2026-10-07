---
title: Root models, generics and forward references
date: 2022-01-18
---

# Root models, generics and forward references

Beyond plain models: models whose value is a single container, generic models and self-references.

```bash
pip install pydantic==1.10.13
```

A custom root type wraps a single value:

```python
from typing import Dict, List
from pydantic import BaseModel, parse_obj_as


class Scores(BaseModel):
    __root__: Dict[str, int]


scores = Scores(__root__={"ann": 3, "bo": 5})
print(scores.__root__["bo"], len(scores.__root__))
```

Validate arbitrary types without defining a model:

```python
nums = parse_obj_as(List[int], ["1", "2", 3])
print(nums, sum(nums))
```

A model can refer to itself:

```python
from typing import Optional


class Node(BaseModel):
    value: int
    next: Optional["Node"] = None


Node.update_forward_refs()
chain = Node(value=1, next=Node(value=2))
print(chain.next.value)
```

Generic models parametrise a field type:

```python
from typing import Generic, TypeVar
from pydantic.generics import GenericModel

T = TypeVar("T")


class Box(GenericModel, Generic[T]):
    item: T


print(Box[int](item="7").item)
```

Copying and constructing without validation:

```python
n = Node(value=5)
m = n.copy(update={"value": 6})
print(n.value, m.value)
print(Node.construct(value=9).value)
```

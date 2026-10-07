---
title: Serialisation and JSON schema in Pydantic
date: 2022-06-21
---

# Serialisation and JSON schema in Pydantic

Models serialise to dicts and JSON, and describe themselves with a JSON schema.

```bash
pip install pydantic==1.10.13
```

An event model that renders its date compactly in JSON:

```python
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class Event(BaseModel):
    name: str
    when: datetime
    attendees: List[str] = []
    room: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.strftime("%Y-%m-%d")}
```

Serialising to JSON:

```python
ev = Event(name="Launch", when="2024-03-05T10:30:00", attendees=["a", "b"])
print(ev.json())
```

Selecting fields when exporting to a dict:

```python
print(ev.dict(exclude_unset=True).keys())
print(ev.dict(exclude={"attendees"})["name"])
```

Parsing JSON text and inspecting the schema:

```python
raw = '{"name": "Demo", "when": "2024-01-01T00:00:00"}'
demo = Event.parse_raw(raw)
print(demo.name, demo.when.year)
print(sorted(Event.schema()["properties"]))
```

Introspecting the declared fields:

```python
print(list(Event.__fields__))
print(Event.__fields__["name"].required)
```

Deep copies with changes:

```python
clone = ev.copy(update={"room": "A1"}, deep=True)
print(clone.room, ev.room)
```

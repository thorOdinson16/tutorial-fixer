---
title: Serialisation and JSON schema in Pydantic
date: 2022-06-21
---

# Serialisation and JSON schema in Pydantic

Models serialise to dicts and JSON, and describe themselves with a JSON schema.

```bash
pip install pydantic==2.13.5
```

An event model that renders its date compactly in JSON:

```python
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_serializer


class Event(BaseModel):
    name: str
    when: datetime
    attendees: List[str] = []
    room: Optional[str] = None

    @field_serializer("when", when_used="json")
    def serialize_when(self, v):
        return v.strftime("%Y-%m-%d")
```

Serialising to JSON:

```python
ev = Event(name="Launch", when="2024-03-05T10:30:00", attendees=["a", "b"])
print(ev.model_dump_json())
```

Selecting fields when exporting to a dict:

```python
print(ev.model_dump(exclude_unset=True).keys())
print(ev.model_dump(exclude={"attendees"})["name"])
```

Parsing JSON text and inspecting the schema:

```python
raw = '{"name": "Demo", "when": "2024-01-01T00:00:00"}'
demo = Event.model_validate_json(raw)
print(demo.name, demo.when.year)
print(sorted(Event.model_json_schema()["properties"]))
```

Introspecting the declared fields:

```python
print(list(Event.model_fields))
print(Event.model_fields["name"].is_required())
```

Deep copies with changes:

```python
clone = ev.model_copy(update={"room": "A1"}, deep=True)
print(clone.room, ev.room)
```

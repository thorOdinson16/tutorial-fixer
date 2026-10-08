---
title: A contacts table with SQLAlchemy Core
date: 2021-11-20
---

# A contacts table with SQLAlchemy Core

Core-only helpers for a small contacts table: create it, check it, and read rows.

```bash
pip install SQLAlchemy==2.1.4
```

Create the table in memory and insert three contacts in one transaction:

```python
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String
engine = create_engine("sqlite://")
meta = MetaData()
contacts = Table("contacts", meta, Column("id", Integer, primary_key=True),
                 Column("name", String(50)), Column("phone", String(20)))
meta.create_all(engine)
rows = [{"name": "Ana", "phone": "555-0101"}, {"name": "Ben", "phone": "555-0102"}, {"name": "Cy", "phone": None}]
with engine.begin() as conn:
    conn.execute(contacts.insert(), rows)
    print("contacts:", len(conn.execute(contacts.select()).fetchall()))
```

Check that the table exists:

```python
from sqlalchemy import inspect
found = inspect(engine).has_table("contacts")
print("table exists:", found)
```

Return the contacts that have a phone number, sorted by name:

```python
from sqlalchemy import select
with engine.connect() as conn:
    stmt = select(contacts.c.name, contacts.c.phone).where(contacts.c.phone.isnot(None))
    rows = conn.execute(stmt.order_by(contacts.c.name)).fetchall()
print([row._mapping["name"] for row in rows])
```

Read the first contact in name order:

```python
with engine.connect() as conn:
    res = conn.execute(contacts.select().order_by(contacts.c.name))
    first = res.fetchone()
print(first.name, first.phone)
```

Count the rows with a plain SQL string:

```python
from sqlalchemy import text
with engine.connect() as conn:
    print("count", conn.scalar(text("SELECT count(*) FROM contacts")))
```

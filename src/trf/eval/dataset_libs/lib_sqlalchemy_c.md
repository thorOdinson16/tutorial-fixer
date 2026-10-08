---
title: A contacts table with SQLAlchemy Core
date: 2021-11-20
---

# A contacts table with SQLAlchemy Core

Core-only helpers for a small contacts table: create it, check it, and read rows.

```bash
pip install SQLAlchemy==1.4.26
```

Create the table in memory and insert three contacts in one transaction:

```python
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String
engine = create_engine("sqlite://")
meta = MetaData()
contacts = Table("contacts", meta,
    Column("id", Integer, primary_key=True),
    Column("name", String(50)),
    Column("phone", String(20)),
)
meta.create_all(engine)
with engine.begin() as conn:
    conn.execute(contacts.insert(), [
        {"name": "Ana", "phone": "555-0101"},
        {"name": "Ben", "phone": "555-0102"},
        {"name": "Cy", "phone": None},
    ])
    print("contacts:", len(conn.execute(contacts.select()).fetchall()))
```

Check that the table exists, two different ways:

```python
print("table exists:", engine.has_table("contacts"))
print("exists:", contacts.exists(engine))
```

Return the contacts that have a phone number, typed with the legacy result class:

```python
from sqlalchemy.engine import ResultProxy
def with_phone(conn) -> ResultProxy:
    return conn.execute(contacts.select().where(contacts.c.phone.isnot(None)).order_by(contacts.c.name))
with engine.connect() as conn:
    print([row.name for row in with_phone(conn).fetchall()])
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
with engine.connect() as conn:
    print("count", conn.scalar("SELECT count(*) FROM contacts"))
```

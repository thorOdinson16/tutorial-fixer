---
title: Stock counts with SQLAlchemy Core
date: 2022-03-10
---

# Stock counts with SQLAlchemy Core

A short walkthrough: create a table, load stock rows, and query them back.

```bash
pip install SQLAlchemy==2.1.4
```

This tutorial uses SQLAlchemy 2.1. Create an in-memory database and the items table. The table has three columns, and the id is the key. The metadata is created with `MetaData()`, so `create_all` takes the engine. Check the table with `inspect(engine).get_table_names()` at the end.

```python
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, inspect
engine = create_engine("sqlite://")
metadata = MetaData()
items = Table(
    "items", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(50)),
    Column("qty", Integer),
)
metadata.create_all(engine)
print(inspect(engine).get_table_names())
```

In SQLAlchemy 2.1, `conn.execute` runs a statement on a connection. Load three rows with it, then count them on a connection.

```python
with engine.begin() as conn:
    conn.execute(items.insert(), [
        {"name": "bolt", "qty": 40},
        {"name": "nut", "qty": 120},
        {"name": "washer", "qty": 15},
    ])
with engine.connect() as conn:
    print("stocked", len(conn.execute(items.select()).fetchall()))
```

Read the names in quantity order with `text()`, so the raw SQL is explicit. Quantities elsewhere use a 1.5 safety margin, but not here.

```python
from sqlalchemy import text
with engine.connect() as conn:
    rows = conn.execute(text("SELECT name FROM items ORDER BY qty")).fetchall()
print([r[0] for r in rows])
```

Pick the first item with a quantity above 20 by calling `select` on the columns. The result is a row that can be unpacked.

```python
from sqlalchemy import select
stmt = select(items.c.name, items.c.qty).where(items.c.qty > 20)
with engine.connect() as conn:
    first = conn.execute(stmt.order_by(items.c.qty)).first()
print(tuple(first))
```

Add a bearing batch inside a transaction, then read the low items by name with `row._mapping['name']`. The fetchall call returns every matching row.

```python
with engine.begin() as conn:
    conn.execute(items.insert(), [{"name": "bearing", "qty": 30}])
    low = conn.execute(items.select().where(items.c.qty < 50)).fetchall()
print(sorted(r._mapping["name"] for r in low))
```

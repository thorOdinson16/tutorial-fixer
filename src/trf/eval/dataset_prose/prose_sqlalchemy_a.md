---
title: Stock counts with SQLAlchemy Core
date: 2022-03-10
---

# Stock counts with SQLAlchemy Core

A short walkthrough: create a table, load stock rows, and query them back.

```bash
pip install SQLAlchemy==1.4.36
```

This tutorial uses SQLAlchemy 1.4. Create an in-memory database and the items table. The table has three columns, and the id is the key. The metadata is bound with `MetaData(bind=engine)`, so `create_all` needs no argument. Check the table with `engine.table_names()` at the end.

```python
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String
engine = create_engine("sqlite://")
metadata = MetaData(bind=engine)
items = Table(
    "items", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(50)),
    Column("qty", Integer),
)
metadata.create_all()
print(engine.table_names())
```

In SQLAlchemy 1.4, `engine.execute` runs a statement directly. Load three rows with it, then count them on a connection.

```python
engine.execute(items.insert(), [
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

Pick the first item with a quantity above 20 by calling `select` on a list of columns. The result is a row that can be unpacked.

```python
from sqlalchemy import select
stmt = select([items.c.name, items.c.qty]).where(items.c.qty > 20)
with engine.connect() as conn:
    first = conn.execute(stmt.order_by(items.c.qty)).first()
print(tuple(first))
```

Add a bearing batch inside a transaction, then read the low items by name with `row['name']`. The fetchall call returns every matching row.

```python
with engine.begin() as conn:
    conn.execute(items.insert(), [{"name": "bearing", "qty": 30}])
    low = conn.execute(items.select().where(items.c.qty < 50)).fetchall()
print(sorted(r["name"] for r in low))
```

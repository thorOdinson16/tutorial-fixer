---
title: Building a book catalogue with SQLAlchemy Core
date: 2022-06-14
---

# Building a book catalogue with SQLAlchemy Core

A short walkthrough: create a table, load a few books, and query them back.

```bash
pip install SQLAlchemy==1.4.36
```

Create an in-memory database and the books table:

```python
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String
engine = create_engine("sqlite://")
metadata = MetaData(bind=engine)
books = Table(
    "books", metadata,
    Column("id", Integer, primary_key=True),
    Column("title", String(100)),
    Column("year", Integer),
)
metadata.create_all()
print(engine.table_names())
```

Load three books with an engine-level insert:

```python
engine.execute(books.insert(), [
    {"title": "Dune", "year": 1965},
    {"title": "Emma", "year": 1815},
    {"title": "Ulysses", "year": 1922},
])
with engine.connect() as conn:
    print("loaded", len(conn.execute(books.select()).fetchall()))
```

Read the titles back in year order with a raw SQL string:

```python
with engine.connect() as conn:
    rows = conn.execute("SELECT title FROM books ORDER BY year").fetchall()
print([r[0] for r in rows])
```

Pick the first book after 1900 with a select, reading the columns by name:

```python
from sqlalchemy import select
stmt = select([books.c.title, books.c.year]).where(books.c.year > 1900)
with engine.connect() as conn:
    first = conn.execute(stmt.order_by(books.c.year)).first()
print(first["title"], first["year"])
```

Add a book inside a transaction and list the pre-1900 ones:

```python
with engine.begin() as conn:
    conn.execute(books.insert(), [{"title": "Persuasion", "year": 1817}])
    old = conn.execute(books.select().where(books.c.year < 1900)).fetchall()
print(sorted(r.title for r in old))
```

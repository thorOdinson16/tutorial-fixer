---
title: Keeping a task list with the SQLAlchemy ORM
date: 2022-10-12
---

# Keeping a task list with the SQLAlchemy ORM

A small projects-and-tasks model, queried with the ORM and then inspected by hand.

```bash
pip install SQLAlchemy==1.4.40
```

Start with an in-memory engine and a Project model:

```python
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, Session
engine = create_engine("sqlite://")
Base = declarative_base()
class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String(50))
print(Project.__tablename__)
```

Add the Task model and create both tables:

```python
class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(100))
    done = Column(Integer, default=0)
    project_id = Column(Integer, ForeignKey("projects.id"))
    project = relationship("Project")
Base.metadata.create_all(engine)
print(sorted(Base.metadata.tables))
```

Add a project with two tasks and count them:

```python
with Session(engine) as s:
    home = Project(name="home")
    s.add_all([home, Task(title="fix sink", project=home), Task(title="paint", project=home, done=1)])
    s.commit()
    print(s.query(Task).count(), "tasks")
```

Reflect the tasks table again to see its columns:

```python
from sqlalchemy import MetaData, Table
audit = MetaData()
audit_tasks = Table("tasks", audit, autoload=True, autoload_with=engine)
print(sorted(c.name for c in audit_tasks.columns))
```

List the tasks that belong to the home project, using the relationship name in the join:

```python
with Session(engine) as s:
    q = s.query(Task).join("project").filter(Project.name == "home")
    print(sorted(t.title for t in q))
```

Check which tables exist and read one project row back as a dict:

```python
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy import select
print(sorted(Inspector.from_engine(engine).get_table_names()))
with engine.connect() as conn:
    first = conn.execute(select(Project.name)).first()
print(dict(first))
```

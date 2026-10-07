"""Shared tiny corpus + fake models for retrieval tests (no network, no model downloads)."""

from __future__ import annotations

import hashlib
import re

import numpy as np

from trf.rag.bm25 import tokenize
from trf.state import Chunk

RST = """\
.. _whatsnew_200:

What's new in 2.0.0 (April 3, 2023)
-----------------------------------

Enhancements
~~~~~~~~~~~~

- Added :meth:`DataFrame.to_json` ``mode`` keyword (:issue:`35849`)

Removal of prior version deprecations/changes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- Removed deprecated :meth:`Series.iteritems`, :meth:`DataFrame.iteritems`, use ``obj.items`` instead (:issue:`45321`)
- Removed deprecated :meth:`Series.append`, :meth:`DataFrame.append`, use :func:`concat` instead (:issue:`35407`)
- Removed ``pd.datetime``, use ``datetime.datetime`` instead
"""

MD = """\
# History

## v2.5.0 (2024-05-01)

### What's Changed

* Add `model_dump_json` indent support by @someone in #1234

### New Contributors

* @newbie made their first contribution

## v1.10.13 (2023-09-27)

* Fix `validator` with `always=True` on nested models, #7000
"""

MIGRATION_MD = """\
# Migration guide

## Changes to `pydantic.BaseModel`

| Pydantic V1 | Pydantic V2 |
| --- | --- |
| `parse_obj` | `model_validate` |

```python
# V1
User.parse_obj(data)
# V2
User.model_validate(data)
```

## Changes to validators

`@validator` is deprecated; use `@field_validator` instead.
"""


def chunk(id, text, version=None, doc_type="changelog", symbol=None, lib="lib"):
    return Chunk(
        id=id,
        text=text,
        library=lib,
        version_introduced=version,
        symbol=symbol,
        doc_type=doc_type,
        source_url=f"https://x/{id}",
    )


class FakeEncoder:
    """Hashed bag-of-words embeddings: similar word overlap -> higher cosine."""

    name = "fake-bow"

    def __init__(self, dim: int = 128):
        self.dim = dim
        self.calls = 0

    def encode(self, texts, *, is_query=False):
        self.calls += 1
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z_]+", t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class FakeScorer:
    """Cross-encoder stand-in: number of shared tokens."""

    def __init__(self):
        self.calls = 0

    def score(self, query, texts):
        self.calls += 1
        q = set(tokenize(query))
        return [float(len(q & set(tokenize(t)))) for t in texts]

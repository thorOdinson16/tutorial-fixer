"""Okapi BM25 over identifier-aware tokens (pure Python; the corpus is a few thousand chunks).

The tokenizer keeps whole identifiers (`parse_obj`, `dtype_backend`) and, for dotted names,
the full name plus each part and adjacent pairs, so `pandas.DataFrame.append` matches a chunk
that says `DataFrame.append` or just `append`.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*|\d+(?:\.\d+)*")


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for m in _TOKEN.finditer(text):
        tok = m.group().lower()
        out.append(tok)
        if "." in tok:
            parts = tok.split(".")
            out.extend(parts)
            out.extend(f"{a}.{b}" for a, b in zip(parts, parts[1:], strict=False) if len(parts) > 2)
    return out


class BM25Index:
    def __init__(self, ids: list[str], texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.ids = ids
        self.k1, self.b = k1, b
        docs = [tokenize(t) for t in texts]
        self.tf = [Counter(d) for d in docs]
        self.dl = [len(d) for d in docs]
        self.avgdl = (sum(self.dl) / len(docs)) if docs else 0.0
        df: Counter[str] = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.postings: dict[str, list[int]] = {}
        for i, c in enumerate(self.tf):
            for t in c:
                self.postings.setdefault(t, []).append(i)

    def search(self, query: str, k: int = 50) -> list[tuple[str, float]]:
        scores: dict[int, float] = {}
        for t in set(tokenize(query)):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                f = self.tf[i][t]
                norm = 1 - self.b + self.b * self.dl[i] / (self.avgdl or 1)
                scores[i] = scores.get(i, 0.0) + idf * f * (self.k1 + 1) / (f + self.k1 * norm)
        top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [(self.ids[i], s) for i, s in top]

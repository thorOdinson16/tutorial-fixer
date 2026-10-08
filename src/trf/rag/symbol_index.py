"""Symbol -> chunk index: which changelog/migration entries actually mention a given symbol.

Built from backticked spans and identifiers inside code fences. Each chunk is registered under
the full dotted name, its last two components and its last component. Lookup tries the most
specific key first and discounts generic names (`dict`, `json`) by how many chunks use them.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

from trf.state import Chunk, Symbol

_SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_NAME = re.compile(r"^@?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_CODE_IDENT = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+|[A-Za-z_]\w*_\w+")
_ARG = re.compile(r"^(?P<callee>.+)\((?P<arg>\w+)\)$")


# How release notes and tutorials usually spell the root of a symbol path.
ALIAS_FOR_PREFIX = {"numpy": "np", "pandas": "pd", "matplotlib.pyplot": "plt"}


def aliased_name(name: str) -> str | None:
    """`numpy.float` -> `np.float`, `matplotlib.pyplot.style.use` -> `plt.style.use`."""
    for prefix, alias in sorted(ALIAS_FOR_PREFIX.items(), key=lambda kv: -len(kv[0])):
        if name == prefix or name.startswith(prefix + "."):
            return alias + name[len(prefix) :]
    return None


def _keys_for(name: str) -> set[str]:
    parts = name.split(".")
    keys = {name, parts[-1]}
    if len(parts) >= 2:
        keys.add(".".join(parts[-2:]))
    return keys


def names_in_chunk(text: str) -> set[str]:
    names: set[str] = set()
    for m in _SPAN.finditer(text):
        nm = _NAME.match(m.group(1).strip())
        if nm:
            names.add(nm.group(1))
    for fence in _FENCE.findall(text):
        names.update(_CODE_IDENT.findall(fence))
    return names


class SymbolIndex:
    def __init__(self, chunks: list[Chunk]):
        self.postings: dict[str, set[str]] = defaultdict(set)
        for c in chunks:
            for name in names_in_chunk(c.text):
                for key in _keys_for(name):
                    self.postings[key].add(c.id)

    def _rarity(self, key: str) -> float:
        return 1.0 / (1.0 + math.log(max(len(self.postings.get(key, ())), 1)))

    def candidate_keys(self, symbol: Symbol) -> list[tuple[str, float]]:
        """(key, weight) from most to least specific for this symbol."""
        name = symbol.name
        m = _ARG.match(name)
        if m:  # keyword argument: the argument name is the distinctive part
            return [(m["arg"], 1.0)]
        parts = [p for p in name.split(".") if p and p != "*"]
        qualified = ".".join(parts)  # chunks often say `pandas.datetime`: rarer than `datetime`
        alias = aliased_name(qualified)  # ...or `np.float`, `pd.datetime`
        if parts and parts[0] == symbol.library:
            parts = parts[1:]
        if not parts:
            return []
        keys = [(alias, 1.0)] if alias else []
        keys += [(qualified, 1.0)] if qualified != ".".join(parts) else []
        keys.append((".".join(parts), 1.0))
        if len(parts) > 2:
            keys.append((".".join(parts[-2:]), 0.8))
        keys.append((parts[-1], 0.8 if len(parts) == 1 else 0.4))
        return keys

    def lookup(self, symbol: Symbol) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        for key, w in self.candidate_keys(symbol):
            for cid in self.postings.get(key, ()):
                s = w * self._rarity(key)
                if s > scores.get(cid, 0.0):
                    scores[cid] = s
        m = _ARG.match(symbol.name)
        if m:  # prefer chunks that also name the callee (e.g. Field for Field(regex))
            callee = m["callee"].split(".")[-1]
            callee_chunks = self.postings.get(callee, set())
            for cid in scores:
                if cid in callee_chunks:
                    scores[cid] *= 1.5
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))

"""Dense retrieval: sentence-embedding encoder + a numpy matrix store persisted to disk.

Corpora are a few thousand chunks, so exact cosine search over a normalised matrix is fast
and avoids a vector-DB dependency. The encoder is a Protocol so tests inject a fake one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

import numpy as np

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Encoder(Protocol):
    name: str

    def encode(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:
        """Return an (n, d) float32 array of L2-normalised embeddings."""
        ...


class SentenceTransformerEncoder:
    def __init__(self, model_name: str = DEFAULT_MODEL):
        from sentence_transformers import SentenceTransformer  # heavy import, kept lazy

        self.name = model_name
        self.model = SentenceTransformer(model_name)

    def encode(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:
        if is_query and "bge" in self.name.lower():
            texts = [_BGE_QUERY_PREFIX + t for t in texts]
        emb = self.model.encode(
            texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False
        )
        return np.asarray(emb, dtype=np.float32)


class DenseIndex:
    def __init__(self, ids: list[str], matrix: np.ndarray, encoder: Encoder):
        self.ids, self.matrix, self.encoder = ids, matrix, encoder

    @classmethod
    def build(
        cls,
        ids: list[str],
        texts: list[str],
        encoder: Encoder,
        cache_dir: Path | None = None,
        tag: str = "corpus",
    ) -> DenseIndex:
        """Embed `texts`, reusing an on-disk matrix when model + content are unchanged."""
        key = hashlib.sha1("\n".join([encoder.name, *ids, *texts]).encode()).hexdigest()[:16]
        f = (cache_dir / "dense" / f"{tag}-{key}.npy") if cache_dir else None
        if f and f.exists():
            return cls(ids, np.load(f), encoder)
        matrix = encoder.encode(texts) if texts else np.zeros((0, 1), dtype=np.float32)
        if f:
            f.parent.mkdir(parents=True, exist_ok=True)
            np.save(f, matrix)
        return cls(ids, matrix, encoder)

    def search(self, query: str, k: int = 50) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        q = self.encoder.encode([query], is_query=True)[0]
        sims = self.matrix @ q
        top = np.argsort(-sims, kind="stable")[:k]
        return [(self.ids[i], float(sims[i])) for i in top]

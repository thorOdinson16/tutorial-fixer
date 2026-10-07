"""Retrieval sanity check against the REAL corpora and models.

    uv run python -m trf.rag.corpus build          # once
    uv run python scripts/retrieval_sanity.py [--show full] [--k 5]

Prints the top-k chunks per known symbol for one config (`--show`), then hit@k / MRR across the
ablation configs: a "hit" is a top-k chunk mentioning both the old symbol and its replacement.
This is a smoke test of retrieval quality, not the Phase 3 eval.
"""

from __future__ import annotations

import argparse

from trf.config import PipelineFlags, get_cache_dir
from trf.rag.corpus import corpus_path, load_corpus
from trf.rag.pipeline import RetrievalPipeline
from trf.state import Symbol

# (symbol, old, target, old token, replacement token): a correct chunk mentions BOTH
S = Symbol
CASES = [
    (S(name="pydantic.BaseModel.parse_obj", library="pydantic", kind="method"), "1.10.13", "2.13.5", "parse_obj", "model_validate"),
    (S(name="pydantic.validator", library="pydantic", kind="function"), "1.10.13", "2.13.5", "@validator", "field_validator"),
    (S(name="pydantic.Field(regex)", library="pydantic", kind="argument"), "1.10.13", "2.13.5", "regex", "pattern"),
    (S(name="pydantic.BaseModel.Config.orm_mode", library="pydantic", kind="config_key"), "1.10.13", "2.13.5", "orm_mode", "from_attributes"),
    (S(name="pydantic.BaseSettings", library="pydantic", kind="class"), "1.10.13", "2.13.5", "BaseSettings", "pydantic-settings"),
    (S(name="pydantic.root_validator", library="pydantic", kind="function"), "1.10.13", "2.13.5", "root_validator", "model_validator"),
    (S(name="pandas.DataFrame.append", library="pandas", kind="method"), "1.3.5", "2.3.3", "append", "concat"),
    (S(name="pandas.DataFrame.iteritems", library="pandas", kind="method"), "1.3.5", "2.3.3", "iteritems", "items"),
    (S(name="pandas.datetime", library="pandas", kind="attribute"), "1.3.5", "2.3.3", "pandas.datetime", "datetime"),
    (S(name="pandas.DataFrame.to_csv(line_terminator)", library="pandas", kind="argument"), "1.3.5", "2.3.3", "line_terminator", "lineterminator"),
]  # fmt: skip

CONFIGS = {
    "bm25 only": dict(use_bm25=True),
    "dense only (cfg 2)": dict(use_dense=True),
    "hybrid RRF (cfg 3)": dict(use_bm25=True, use_dense=True),
    "hybrid+rerank": dict(use_bm25=True, use_dense=True, use_rerank=True),
    "hybrid+rerank+boost": dict(use_bm25=True, use_dense=True, use_rerank=True, use_version_boost=True),
    "full (cfg 4)": dict(use_bm25=True, use_dense=True, use_rerank=True, use_version_boost=True, use_symbol_index=True),
}  # fmt: skip


def flags(**on: bool) -> PipelineFlags:
    return PipelineFlags(**{f: on.get(f, False) for f in PipelineFlags.model_fields})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", default="full (cfg 4)", choices=list(CONFIGS))
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    cache = get_cache_dir()
    corpora = {lib: load_corpus(corpus_path(lib, cache)) for lib in ("pydantic", "pandas")}
    pipes = {
        (name, lib): RetrievalPipeline(corpora[lib], flags(**cfg), cache_dir=cache, tag=lib)
        for name, cfg in CONFIGS.items()
        for lib in corpora
    }

    def good(chunk, old_tok, new_tok):
        low = chunk.text.lower()
        return old_tok.lower() in low and new_tok.lower() in low

    print(f"=== top-{args.k} for config: {args.show}  (* = mentions old AND replacement) ===")
    for sym, old, target, old_tok, new_tok in CASES:
        hits = pipes[(args.show, sym.library)].retrieve_for_symbol(
            sym, old=old, target=target, k=args.k
        )
        print(f"\n## {sym.name}   ({old} -> {target}; expect '{old_tok}' + '{new_tok}')")
        for i, h in enumerate(hits, 1):
            c = h.chunk
            body = c.text.split("\n\n", 1)[-1].replace("\n", " ")[:100]
            mark = "*" if good(c, old_tok, new_tok) else " "
            print(f" {i}{mark} {h.score:.3f} {c.id.split(':', 1)[1]:<26} v{c.version_introduced or '-':<7} "
                  f"{c.doc_type:<9} via={'+'.join(h.via):<22} {body}")  # fmt: skip

    print(f"\n=== strict hit@{args.k} / MRR@{args.k} per config ===")
    w = 11
    print(
        f"{'config':<22}"
        + "".join(f"{s.name.split('.')[-1][: w - 1]:>{w}}" for s, *_ in CASES)
        + "   hit@k   MRR"
    )
    for name in CONFIGS:
        cells, hits_n, rr = [], 0, 0.0
        for sym, old, target, old_tok, new_tok in CASES:
            hits = pipes[(name, sym.library)].retrieve_for_symbol(
                sym, old=old, target=target, k=args.k
            )
            rank = next((i for i, h in enumerate(hits, 1) if good(h.chunk, old_tok, new_tok)), None)
            hits_n += rank is not None
            rr += 1 / rank if rank else 0.0
            cells.append(f"{rank or '-':>{w}}")
        print(f"{name:<22}" + "".join(cells) + f"   {hits_n}/{len(CASES)}   {rr / len(CASES):.2f}")


if __name__ == "__main__":
    main()

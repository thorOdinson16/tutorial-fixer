# Evaluation: datasets, configs, metrics, reproduction

How the evaluation sets were built and validated, what each config means, and the commands that reproduce every number.

## Development phases

| Phase | Content | State |
|---|---|---|
| 0 | Scaffold, config, LLM wrapper, state, graph with conditional cycle, sandbox image + smoke test | done |
| 1 | ingest / extract / resolve_versions / sandbox, baseline on 3 tutorials | done |
| 2 | Corpus, BM25, dense, RRF, rerank, symbol index, retrieval sanity script | done |
| 3 | diff, rewrite, assemble (implemented and unit-tested); 15-tutorial eval dataset with hand-fixed references; eval runner | done; evaluated (see Results) |
| 4 | Sandbox feedback loop (`query_rewrite`) and unresolved handling: implemented and tested with a scripted LLM and sandbox; full ablation needs a real model | done; evaluated as config `5_loop` |
| 5 | FastAPI service + side-by-side diff UI (`trf.api.main`), tested with a fake runner and checked in headless Chrome | done |
| 6 | Polish: demo script, limitations, ablation, results | done (single-run results, see caveats) |
| 7 | Seven libraries (NumPy, SQLAlchemy, scikit-learn, Matplotlib, SciPy added), held-out and library eval sets, `protect_working_blocks`, plain-language diagnoses, `trf-serve`, spend cap | done |
| 8 | Defect repair (static import pre-repair, verified import/package repairs, suggested definitions), guarded prose updates and deprecation-warning fixes, with defect and prose eval sets | done (see Results) |

## Evaluation dataset

`src/trf/eval/dataset/` holds 15 hand-built tutorials (6 Pydantic v1→v2, 8 pandas 1.x→2.x, 1 mixed);
`dataset_heldout/` 6 more written afterwards, and `dataset_libs/` 15 for NumPy, SQLAlchemy,
scikit-learn and Matplotlib (3 each). Each has four files:

| file | content |
|---|---|
| `NAME.md` | the outdated tutorial (valid in its original pinned environment) |
| `NAME.fixed.md` | hand-fixed reference: identical prose, only code differs, passes in the target env |
| `NAME.assertions.json` | per-block expected stdout / snippet, so "runs without error" is not the only criterion |
| `NAME.symbols.json` | hand-labelled verdicts for the symbols that changed (84 in total) |

`uv run python -m trf.eval.validate_dataset` proves the dataset is sound: every original must pass
in its original environment (Python 3.10, pinned versions) and every reference must pass in the target
environment (Python 3.12, pandas 2.3.3 / pydantic 2.13.5), with identical prose and block structure.
All 33 pass both checks (`--dataset-dir` selects a set). Verdict labels: `renamed` (same thing, new name), `signature_changed`
(argument / keyword / config key), `removed`, `behavior_changed` (same API, different semantics).

## Eval configs and metrics

`uv run python -m trf.eval.run_eval` (resumable; logs every run as JSON under `runs/eval/`):

| config | meaning |
|---|---|
| `0_original` | untouched tutorial in the target env (how broken is it?) |
| `1_llm_only` | LLM rewrites each block from its own knowledge: no retrieval, no verdicts |
| `2_dense` | + dense retrieval, cited diff verdicts |
| `3_hybrid` | + BM25 fused with RRF |
| `4_full` | + cross-encoder rerank, version boost, symbol index |
| `5_loop` | + sandbox feedback loop (Phase 4) |
| `6_protect` | `5_loop` + never edit a block that already runs (opt-in) |
| `7_repair` | `6_protect` + defect repair (forgotten imports and packages) |
| `8_prose` | `6_protect` + prose updates |
| `9_all` | `6_protect` + repair + prose |
| `10_warnings` | `6_protect` + warning fixes |
| `11_everything` | `6_protect` + repair + prose + warning fixes |

Metrics (`trf/eval/metrics.py`): block and tutorial **pass rate** (primary; assertions included),
symbol-level **diff accuracy** against the hand labels, **minimal-edit** ratio (original lines the
system changed that the reference did not, plus prose segments altered), and **unresolved rate**.
Note that a tutorial can pass without being modernised (Pydantic v2 still runs the deprecated
`@validator`), which is why pass rate alone is not the whole story.

## Phase 1 baseline: how broken are the original tutorials?

`uv run python -m trf.eval.baseline [--mode target|old]` runs each tutorial's ORIGINAL code in a
Docker env with exact pinned versions and logs per-block results.

| case | python blocks | pass (target: pandas 2.3.3 / pydantic 2.13.5) | pass (control: original pins) |
|---|---|---|---|
| pydantic_user_models | 5 | 0 | 5 |
| pandas_sales_analysis | 6 | 1 | 6 |
| mixed_settings_and_frames | 5 | 0 | 5 |

The control column shows the tutorials are valid in their original environments (Python 3.10), so
every target-environment failure is genuine version rot.

## Phase 2 retrieval smoke test

`scripts/retrieval_sanity.py`, 10 known symbols, a hit = a top-5 chunk mentions both the old symbol
and its replacement:

| config | hit@5 | MRR@5 |
|---|---|---|
| BM25 only | 10/10 | 0.87 |
| dense only | 7/10 | 0.53 |
| hybrid RRF | 9/10 | 0.62 |
| + rerank | 9/10 | 0.78 |
| + rerank + version boost | 9/10 | 0.80 |
| + symbol index (full) | 10/10 | 0.90 |

Caveats: 10 cases, and the rerank blend weight (0.5) was tuned on the same cases, so this is a smoke
test, not a result. BM25 alone is a strong baseline on identifier queries; the full pipeline's edge
over it is small here. The end-to-end pass rate in the Phase 3/4 eval is the real comparison.

## Reproducing the evaluation

```bash
uv run python -m trf.eval.validate_dataset                      # dev set (Docker)
uv run python -m trf.eval.validate_dataset --dataset-dir src/trf/eval/dataset_heldout
uv run python -m trf.eval.validate_dataset --dataset-dir src/trf/eval/dataset_libs
uv run python -m trf.eval.prose_check --dataset-dir src/trf/eval/dataset_prose
uv run python scripts/make_defect_set.py                          # regenerates dataset_defects/
uv run python -m trf.eval.run_eval [--dataset-dir DIR --out runs/DIR]   # resumable; runs/<out>/results.md
uv run python scripts/check_case.py CASE... [--dataset-dir DIR]   # one case: old env / target env / reference
```

`--retry-errors` re-runs cases whose log recorded a crash, and a spent budget stops the eval
cleanly (see Spend cap). Every log records its own billed tokens.

## How well does `query_rewrite` read real failures?

Run over the 48 failing blocks of config 0 (real tracebacks from the Docker sandbox): 31 name a
library symbol and 29 of those name one the dataset labels as changed. Most of the other 19 are
`NameError`s cascading from an earlier failing block, which the loop resolves by fixing the root
block, and output mismatches with no API clue in the message. This measures the error parser only,
not whether the rewrites then pass.

## Plumbing check with an oracle (not a model result)

`scripts/oracle_check.py` swaps the LLM for an oracle that returns the dataset's hand labels (diff)
and hand-fixed blocks (rewrite), with the rest real: retrieval over the built corpus and the Docker
sandbox. In `forget` mode the oracle finds nothing on the first diff pass, so only the feedback loop
can recover. Over all 15 tutorials (`5_loop`, max 3 loops): **73 of 82 blocks pass, 10 of 15
tutorials fully**, versus 41% of blocks / 1 of 15 for the untouched tutorials. The 9 blocks left
unresolved are errors that name no library symbol (changed validation behaviour, a changed default
that alters output). This shows the wiring and the error parser work end to end and is an upper
bound for those parts; it says nothing about how well a real model classifies or rewrites.

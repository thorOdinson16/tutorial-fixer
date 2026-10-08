# Tutorial Rot Fixer

A multi-agent system that takes an outdated Python tutorial (Markdown containing Python code) and
produces an updated version whose code **actually runs against current library versions**.
Verification by execution is the core of the project: nothing is reported as passing unless it ran
in a sandbox.

v1 scope: Python tutorials, Pydantic v1 → v2 and pandas 1.x → 2.x (pluggable via `LibraryConfig`).
Input: a Markdown file. Output: updated Markdown plus a change log (what changed, why, source
links, run status per code block).

**Status:** all pipeline code, the eval harness, the feedback loop and the API/UI are built and
tested (171 tests, a mocked LLM and sandbox where needed). The end-to-end eval (configs 0–5, 15 tutorials, `gpt-6-luna`) has run once; see
[Results](#results) for the numbers and, as importantly, what they do not show.
Setup instructions are in [SETUP.md](SETUP.md).

## How it works

```mermaid
flowchart LR
    A[ingest] --> B[extract] --> C[resolve_versions] --> D[retrieve] --> E[diff] --> F[rewrite] --> G[sandbox]
    G -->|all pass, or loop_count >= max_loops| H[assemble]
    G -->|failures and loop_count < max_loops| Q[query_rewrite]
    Q -->|a failing block names a library symbol| D
    Q -->|nothing to retry| H
```

`graph.py` is a LangGraph `StateGraph` with a conditional cycle. Every node is a pure-ish function
(`GraphState` in, state patch out), typed with Pydantic, and unit-testable with a mocked LLM.

| Node | What it does |
|---|---|
| `ingest` | Lossless Markdown split into prose and code segments (re-rendering gives back the exact input), language detection, tutorial date inference, version pins from `pip install` lines |
| `extract` | AST analysis: libraries, imported/used symbols (`pandas.DataFrame.append`, `pydantic.Field(regex)`, `...Config.orm_mode`), install commands, extra third-party packages |
| `resolve_versions` | Old version from exact pin, then range pin, then tutorial date (never guessed); target = latest PyPI release within the migration's target major, or a user pin |
| `retrieve` | Per-symbol retrieval over the corpus (see below) |
| `diff` | One batched LLM call per library classifies symbols `unchanged / renamed / signature_changed / removed / behavior_changed`; every verdict must cite chunk ids actually shown to the model |
| `rewrite` | Surgical per-block LLM edits (prose is never touched); output must parse and every change must map to a cited verdict. Deterministic edits for install pins and newly required packages |
| `sandbox` | Builds a cached image with the exact target versions, then runs all blocks in one network-less container with per-block status, traceback, stdout and assertions |
| `query_rewrite` | Loop only: reads each failing block's traceback and names the library symbol that broke (`'DataFrame' object has no attribute 'append'` -> `pandas.DataFrame.append`, `unexpected keyword argument 'squeeze'` -> `pandas.read_csv(squeeze)`). Deterministic pattern matching, no LLM call. Only symbols still lacking evidence or a verdict are retrieved and diffed again; the diff prompt gets the observed failure. If no symbol can be named the block stays failing and ends `unresolved` |
| `assemble` | Final Markdown + change log. Blocks still failing become `unresolved` with their last error; never reported as passing |

### Retrieval

Corpus per library from raw Markdown/RST on GitHub (migration guides, changelogs / whatsnew, docs),
chunked by section with heading path and `version_introduced` metadata. Stages, each behind a flag
in `PipelineFlags` so the eval can ablate them:

```
symbol index ─┐
BM25 ─────────┼─ RRF ─ version filter ─ cross-encoder (blended with fused score) ─ version boost ─ top-k
dense ────────┘
```

BM25 uses an identifier-aware tokenizer (`parse_obj`, `DataFrame.append` stay whole); dense uses
`BAAI/bge-small-en-v1.5` with a numpy store; reranking uses `ms-marco-MiniLM-L-6-v2`; the
symbol index maps each symbol to the changelog entries that mention it.

### Sandbox

All generated code runs only in Docker: dependencies are installed at image build time, then every
run uses `--network none`, a read-only filesystem, dropped capabilities and CPU/memory/pid/time
limits. Blocks execute in one shared namespace (tutorials are cumulative) through a driver that
`exec`s each block separately, so each gets its own result even after an earlier failure. Optional
per-block assertions (expected stdout substrings, or a snippet run in the tutorial's namespace)
make "runs without error" not the only pass criterion.

### Evidence discipline

- `DiffVerdict` and `ChangeLogEntry` reject uncited claims at the model level.
- A verdict citing ids that were never retrieved is dropped, not kept.
- `llm_prior` change entries exist only for the no-retrieval baseline and are labelled "uncited".
- A rewritten block with no attributable citation is rejected and the original kept.

## Repository layout

```
.
  pyproject.toml, uv.lock, README.md, SETUP.md
  src/trf/
    config.py        env settings, PipelineFlags, LibraryConfig registry
    llm.py           provider-agnostic wrapper (anthropic / openai-compatible / ollama), structured output, disk cache
    state.py         Pydantic models + GraphState
    graph.py         LangGraph wiring
    pypi.py          release metadata for version resolution
    nodes/           ingest, extract, resolve_versions, retrieve, diff, rewrite, sandbox, query_rewrite, assemble
    rag/             corpus, chunking, bm25, dense, hybrid (RRF), rerank, symbol_index, pipeline
    sandbox_runner/  Dockerfile + in-container runner
    eval/            dataset/ (15 tutorials), dataset_heldout/ (6), loader, baseline, validate_dataset, metrics, run_eval
    api/             FastAPI service + static/index.html (single-file UI)
  scripts/           retrieval_sanity.py, demo.py
  tests/
```

## Status

| Phase | Content | State |
|---|---|---|
| 0 | Scaffold, config, LLM wrapper, state, graph with conditional cycle, sandbox image + smoke test | done |
| 1 | ingest / extract / resolve_versions / sandbox, baseline on 3 tutorials | done |
| 2 | Corpus, BM25, dense, RRF, rerank, symbol index, retrieval sanity script | done |
| 3 | diff, rewrite, assemble (implemented and unit-tested); 15-tutorial eval dataset with hand-fixed references; eval runner | done; evaluated (see Results) |
| 4 | Sandbox feedback loop (`query_rewrite`) and unresolved handling: implemented and tested with a scripted LLM and sandbox; full ablation needs a real model | done; evaluated as config `5_loop` |
| 5 | FastAPI service + side-by-side diff UI (`trf.api.main`), tested with a fake runner and checked in headless Chrome | done |
| 6 | Polish: demo script, limitations, ablation instructions done; ablation **results** need the real eval | done (single-run results, see caveats) |

### Evaluation dataset

`src/trf/eval/dataset/` holds 15 hand-built tutorials (6 Pydantic v1→v2, 8 pandas 1.x→2.x, 1 mixed).
Each has four files:

| file | content |
|---|---|
| `NAME.md` | the outdated tutorial (valid in its original pinned environment) |
| `NAME.fixed.md` | hand-fixed reference: identical prose, only code differs, passes in the target env |
| `NAME.assertions.json` | per-block expected stdout / snippet, so "runs without error" is not the only criterion |
| `NAME.symbols.json` | hand-labelled verdicts for the symbols that changed (84 in total) |

`uv run python -m trf.eval.validate_dataset` proves the dataset is sound: every original must pass
in its original environment (Python 3.10, pinned versions) and every reference must pass in the target
environment (Python 3.12, pandas 2.3.3 / pydantic 2.13.5), with identical prose and block structure.
All 15 pass both checks. Verdict labels: `renamed` (same thing, new name), `signature_changed`
(argument / keyword / config key), `removed`, `behavior_changed` (same API, different semantics).

### Eval configs and metrics

`uv run python -m trf.eval.run_eval` (resumable; logs every run as JSON under `runs/eval/`):

| config | meaning |
|---|---|
| `0_original` | untouched tutorial in the target env (how broken is it?) |
| `1_llm_only` | LLM rewrites each block from its own knowledge: no retrieval, no verdicts |
| `2_dense` | + dense retrieval, cited diff verdicts |
| `3_hybrid` | + BM25 fused with RRF |
| `4_full` | + cross-encoder rerank, version boost, symbol index |
| `5_loop` | + sandbox feedback loop (Phase 4) |

Metrics (`trf/eval/metrics.py`): block and tutorial **pass rate** (primary; assertions included),
symbol-level **diff accuracy** against the hand labels, **minimal-edit** ratio (original lines the
system changed that the reference did not, plus prose segments altered), and **unresolved rate**.
Note that a tutorial can pass without being modernised (Pydantic v2 still runs the deprecated
`@validator`), which is why pass rate alone is not the whole story.

### Phase 1 baseline: how broken are the original tutorials?

`uv run python -m trf.eval.baseline [--mode target|old]` runs each tutorial's ORIGINAL code in a
Docker env with exact pinned versions and logs per-block results.

| case | python blocks | pass (target: pandas 2.3.3 / pydantic 2.13.5) | pass (control: original pins) |
|---|---|---|---|
| pydantic_user_models | 5 | 0 | 5 |
| pandas_sales_analysis | 6 | 1 | 6 |
| mixed_settings_and_frames | 5 | 0 | 5 |

The control column shows the tutorials are valid in their original environments (Python 3.10), so
every target-environment failure is genuine version rot.

### Phase 2 retrieval smoke test

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

## Running it

```bash
uv sync --extra rag --extra api
uv run python -m trf.rag.corpus build            # once
export TRF_LLM_PROVIDER=... TRF_LLM_MODEL=... TRF_LLM_API_KEY=...   # see SETUP.md
uv run python scripts/demo.py pandas_sales_analysis                   # one tutorial, CLI summary
uv run uvicorn trf.api.main:app                                       # service + UI on :8000
```

The UI (`/`) takes pasted or uploaded Markdown and shows, per code block, the original and updated
code side by side, a status badge, the error of any unresolved block, and every change with its
cited source links (uncited `llm_prior` edits are labelled as such). API: `POST /api/jobs`,
`GET /api/jobs/{id}`, `GET /api/jobs/{id}/download`. Jobs are held in memory and run one at a time.

## Reproducing the evaluation

```bash
uv run python -m trf.eval.validate_dataset        # dataset sanity (Docker); passes today
uv run python -m trf.eval.run_eval                # configs 0-5, resumable, logs in runs/eval/
uv run python -m trf.eval.run_eval --configs 4_full 5_loop --cases pandas_timeseries --force
```

`runs/eval/results.md` is regenerated after every case, and `--retry-errors` re-runs cases whose
log recorded a crash (e.g. a network failure). The numbers are in [Results](#results).

### How well does `query_rewrite` read real failures?

Run over the 48 failing blocks of config 0 (real tracebacks from the Docker sandbox): 31 name a
library symbol and 29 of those name one the dataset labels as changed. Most of the other 19 are
`NameError`s cascading from an earlier failing block, which the loop resolves by fixing the root
block, and output mismatches with no API clue in the message. This measures the error parser only,
not whether the rewrites then pass.

### Plumbing check with an oracle (not a model result)

`scripts/oracle_check.py` swaps the LLM for an oracle that returns the dataset's hand labels (diff)
and hand-fixed blocks (rewrite), with the rest real: retrieval over the built corpus and the Docker
sandbox. In `forget` mode the oracle finds nothing on the first diff pass, so only the feedback loop
can recover. Over all 15 tutorials (`5_loop`, max 3 loops): **73 of 82 blocks pass, 10 of 15
tutorials fully**, versus 41% of blocks / 1 of 15 for the untouched tutorials. The 9 blocks left
unresolved are errors that name no library symbol (changed validation behaviour, a changed default
that alters output). This shows the wiring and the error parser work end to end and is an upper
bound for those parts; it says nothing about how well a real model classifies or rewrites.

## Results

Model `gpt-6-luna` via the OpenAI API, temperature 0, 15 tutorials (the dataset above), Docker
sandbox, max 4 loops. One run per config. Pass rates count a block as passing only if it ran in the
target environment and met its assertions.

| config | cases | block pass | tutorial pass | diff acc. | changed recall | verdict exact | unrelated edits | prose edits | unresolved | LLM calls | s/case |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0_original | 15 | 41% | 7% | n/a | n/a | n/a | 0% | 0 | 0% | 0.0 | 1 |
| 1_llm_only | 15 | 87% | 47% | n/a | n/a | n/a | 1% | 0 | 13% | 5.5 | 42 |
| 2_dense | 15 | 67% | 27% | 57% | 56% | 29% | 1% | 0 | 33% | 6.5 | 6 |
| 3_hybrid | 15 | 71% | 33% | 61% | 64% | 43% | 1% | 0 | 29% | 6.6 | 6 |
| 4_full | 15 | 76% | 33% | 53% | 85% | 55% | 1% | 0 | 24% | 7.5 | 28 |
| 5_loop | 15 | 88% | 53% | 55% | 88% | 58% | 1% | 0 | 12% | 9.5 | 29 |

Reading it:

- **The pipeline fixes most of the rot.** Untouched tutorials: 41% of blocks, 1 of 15 tutorials.
  Full pipeline with the loop (`5_loop`): 88% of blocks, 8 of 15 tutorials.
- **Retrieval alone did not beat the no-retrieval baseline on pass rate.** `1_llm_only` (the model
  rewriting from its own knowledge, no evidence, edits uncited) reaches 87%; `2_dense`..`4_full`
  reach 67-76%. These configs only edit a block when a verdict backed by retrieved evidence covers
  it, so a missed verdict leaves the block broken, while the baseline edits freely. What retrieval
  buys is **cited, auditable edits**, and changed-symbol recall rising from 56% (dense) to 85%
  (full): the hybrid, rerank and symbol-index stages help the diff stage find what changed.
- **The sandbox loop is what closes the gap**: `4_full` 76% to `5_loop` 88%, with no cost in
  unrelated edits (1% everywhere) and no prose edits at all.
- **`5_loop` vs `1_llm_only` (88% vs 87%) is a tie** within noise. The claim supported here is
  "cited and verified edits at no loss of pass rate", not "better pass rate".
- Diff accuracy (55% for `5_loop`) is the weakest number; much of the remaining failure is
  behaviour changes that produce wrong output without an error naming any symbol.

### Held-out check

`src/trf/eval/dataset_heldout/` has 6 further tutorials (3 pandas, 2 pydantic, 1 mixed) built after
the system was tuned, around APIs the dev set does not use (`Float64Index`, `date_range(closed)`,
`dt.weekofyear`, `Field(const)`, `ConstrainedInt`, `Required`, `get_loc(method)`, ...). Same
validation as the dev set (`validate_dataset --dataset-dir ...`: originals pass in their old
environment, references pass in the target one, 4 of each case's python blocks break on the new
versions). Run once with no code changes in between:

| config | cases | block pass | tutorial pass | diff acc. | changed recall | verdict exact | unrelated edits | prose edits | unresolved | LLM calls | s/case |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0_original | 6 | 29% | 0% | n/a | n/a | n/a | 0% | 0 | 0% | 0.0 | 1 |
| 1_llm_only | 6 | 97% | 83% | n/a | n/a | n/a | 0% | 0 | 3% | 5.7 | 18 |
| 2_dense | 6 | 76% | 0% | 71% | 61% | 57% | 0% | 0 | 24% | 6.5 | 41 |
| 3_hybrid | 6 | 79% | 33% | 76% | 74% | 57% | 0% | 0 | 21% | 6.5 | 40 |
| 4_full | 6 | 94% | 67% | 57% | 100% | 83% | 1% | 0 | 6% | 7.5 | 69 |
| 5_loop | 6 | 97% | 83% | 56% | 100% | 83% | 1% | 0 | 3% | 7.8 | 32 |

The ordering holds: dense < hybrid < full < loop, the loop adds a few points over `4_full`, and
`5_loop` ties `1_llm_only` on pass rate (97% each) while its edits are cited. Changed-symbol recall
reaches 100% for the full pipeline. Caveats: 6 tutorials (one block is about 3 points), one run,
and the tutorials were written by a smaller model from a spec, so they are cleaner and shorter than
real-world tutorials. They support "the dev-set result was not just tuning", not tight estimates.

Caveats: the model, prompts and the matcher were debugged against this same 15-tutorial dataset
while the eval ran (see below), so the numbers are tuned-on-the-test-set optimistic for
`2_dense`..`5_loop`; `1_llm_only` was not tuned. The held-out check above is the untuned test.

What the first runs fixed (all committed, with tests): the OpenAI backend now sends
`max_completion_tokens`; the diff accepts symbols echoed with a ` (kind)` suffix; evidence is
cut around the symbol instead of from the start (a long changelog chunk hid the
`pandas.datetime` removal past the cut); prompts forbid "modernising" edits (a first run added
`engine="pyarrow"` to a working call); a block broken by the system's own edit is retried; and an
edit reported under a bare callee (`pandas.read_csv`) is attributed to all matching verdicts
instead of being dropped. The pre-fix numbers were `4_full` 72% and `5_loop` 84% (kept locally in `runs/eval_v1/`; `runs/` is not committed).

## Known limitations

- One model, one run per config, 15 tutorials, temperature 0: a single block is about 1 point of
  block pass rate, so differences of a few points are noise. A reasoning model on a small local GPU
  returned empty replies on every diff call, which is why the eval used a hosted model.
- `query_rewrite` is pattern matching on common Python errors. Errors that carry no symbol (a
  changed default producing different output, cascades from an earlier block) leave the block
  `unresolved` rather than guessed at. The loop's gain is measured in Results.
- The API keeps jobs in memory (lost on restart, 100 kept) and runs one job at a time.


- Fenced blocks indented more than three spaces (e.g. inside list items) are treated as prose.
- Method-call symbol extraction uses a light, flow-insensitive type tracker, not real type inference.
- Blocks share one namespace, so a failing early block can make later blocks fail too; those are
  tagged "may be a downstream effect" in `last_error`, which is a hint, not a diagnosis.
- Prose is never edited, so prose that mentions old versions or APIs stays as written.
- Install-block updates are deterministic and only handle simple `pip install` lines.
- With a reasoning model on a small local GPU each LLM call takes tens of seconds; the diff stage is
  batched per library for this reason.

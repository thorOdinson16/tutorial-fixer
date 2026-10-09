# Tutorial Rot Fixer

A multi-agent system that takes an outdated Python tutorial (Markdown containing Python code) and
produces an updated version whose code **actually runs against current library versions**.
Verification by execution is the core of the project: nothing is reported as passing unless it ran
in a sandbox.

Scope: Python tutorials and six libraries, each with its own release-note corpus: **pandas** 1.x → 2.x,
**pydantic** v1 → v2, **NumPy** 1.x → 2.x, **SQLAlchemy** 1.x → 2.x, **scikit-learn** 0.x → 1.x and
**Matplotlib** 2.x/3.x → latest 3.x. Libraries are pluggable: see [Adding a library](#adding-a-library).
Input: a Markdown file. Output: updated Markdown plus a change log (what changed, why, source
links, run status per code block).

**Status:** complete and evaluated. The pipeline, feedback loop, six-library corpora, eval harness
and web UI are built and tested (229 tests, a mocked LLM and sandbox where needed). On 33 evaluation
tutorials the full pipeline runs 93% of code blocks (36% untouched); on 20 real tutorials 87% (75%
untouched). See [Results](#results) for the numbers and, as importantly, what they do not show.
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
| 6 | Polish: demo script, limitations, ablation, results | done (single-run results, see caveats) |
| 7 | Six libraries (NumPy, SQLAlchemy, scikit-learn, Matplotlib added), held-out and library eval sets, `protect_working_blocks`, plain-language diagnoses, `trf-serve`, spend cap | done |

### Evaluation dataset

`src/trf/eval/dataset/` holds 15 hand-built tutorials (6 Pydantic v1→v2, 8 pandas 1.x→2.x, 1 mixed);
`dataset_heldout/` 6 more written afterwards, and `dataset_libs/` 12 for NumPy, SQLAlchemy,
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
| `6_protect` | `5_loop` + never edit a block that already runs (opt-in) |

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
# put TRF_LLM_PROVIDER / TRF_LLM_MODEL / TRF_LLM_API_KEY in .env (see SETUP.md)
uv run trf-serve                       # checks Docker + model + corpora, builds what is missing,
                                       # starts the service and opens the UI on :8000
uv run python scripts/demo.py pandas_sales_analysis   # or: one tutorial from the command line
```

`scripts/demo.py` and the service leave blocks that already run untouched by default (the
`protect_working_blocks` precheck, described in Results); `--no-protect` switches that off.

The UI (`/`) takes pasted or uploaded Markdown and shows, per code block, the original and updated
code side by side, a status badge, the error of any unresolved block, and every change with its
cited source links (uncited `llm_prior` edits are labelled as such). API: `POST /api/jobs`,
`GET /api/jobs/{id}`, `GET /api/jobs/{id}/download`. Jobs are held in memory and run one at a time.

## Results

Model `gpt-6-luna` via the OpenAI API, temperature 0, Docker sandbox, max 4 loops, one run per
configuration. A block passes only if it ran in the target environment and met its assertions.
Three evaluation sets, 33 tutorials in all (181 python blocks):

| set | tutorials | libraries | how it was made |
|---|---|---|---|
| dev | 15 | pandas, pydantic | hand-written; used while building the system |
| held-out | 6 | pandas, pydantic | written later around APIs the dev set does not use |
| libs | 12 | NumPy, SQLAlchemy, scikit-learn, Matplotlib (3 each) | written by subagents from a spec |

Every tutorial is validated in Docker (`validate_dataset --dataset-dir ...`): the original passes in
its old environment, the hand-fixed reference passes in the target one, and several blocks of the
original really break there.

**Block pass rate / tutorials fully passing**

| config | dev (15) | held-out (6) | libs (12) | all 33 |
|---|---|---|---|---|
| `0_original` (untouched) | 41% / 1 | 29% / 0 | 32% / 0 | 36% / 1 |
| `1_llm_only` (no retrieval) | 87% / 7 | 97% / 5 | 97% / 10 | 92% / 22 |
| `2_dense` | 67% / 4 | 76% / 0 | 63% / 1 | 67% / 5 |
| `3_hybrid` | 71% / 5 | 82% / 2 | 71% / 3 | 73% / 10 |
| `4_full` | 77% / 5 | 94% / 4 | 77% / 6 | 80% / 15 |
| `5_loop` (+ sandbox loop) | 88% / 8 | 97% / 5 | 98% / 11 | 93% / 24 |
| `6_protect` (+ keep working blocks) | 88% / 8 | 97% / 5 | 98% / 11 | 93% / 24 |

Reading it:

- **The pipeline fixes most of the rot**: 36% of blocks run untouched, 93% after `5_loop`.
- **Every retrieval stage helps, and the loop helps most.** Dense < hybrid < full < loop on all three
  sets. Without the loop, a block is edited only when a cited verdict covers it, so a missed verdict
  leaves it broken (80% for `4_full`); the loop finds the symbol from the sandbox error, retrieves
  with the error message, and closes the gap.
- **`5_loop` ties the no-retrieval baseline on pass rate** (93% vs 92%, 24 vs 22 tutorials fully
  passing), within noise at this sample size. What retrieval adds is **cited, auditable edits**
  (each change links to the release note that justifies it, `1_llm_only` edits are labelled uncited)
  and changed-symbol recall of 88-100%.
- **`6_protect` leaves pass rate unchanged and makes ~12% fewer edits** (169 change-log entries vs
  192) and ~1.3 fewer LLM calls per tutorial, because it never edits a block that already runs.
- **Per library (libs set, blocks passing):** NumPy 15/15, scikit-learn 16/16, Matplotlib 18/18,
  SQLAlchemy 15/16 for `5_loop`, against 14/15, 16/16, 17/18 and 16/16 for `1_llm_only`. Diff
  accuracy is the weakest metric (57-61%); most misses are behaviour changes with no error.

**Caveats.** One model, one run per config. A block is about 0.5% of the pooled pass rate, so
differences of a point or two are noise. The dev and libs sets were used to find and fix problems
(below), so their numbers are optimistic; the held-out set was written after the system was tuned
and was re-run after every later change (97% throughout), which is the cleaner check. The libs and
held-out tutorials were written by smaller models from a spec, so they are cleaner and shorter than
real tutorials.

### What running the real model taught us (all fixed, with tests)

OpenAI's newer models need `max_completion_tokens`; the diff dropped every verdict whose symbol the
model echoed as `name (kind)`; evidence was clipped before the key sentence; a model adding
`engine="pyarrow"` to working code; a block broken by the system's own edit was not retried; a
correct edit was dropped as "unattributed" when the model named no symbol (it is now attributed to
the verdicts whose symbols appear in the lines it changed); retrieval never found `np.float`
because notes say `np`, not `numpy` (aliases `np`/`pd`/`plt` are now searched); errors that name no
function (`tuple indices must be integers... not str` is SQLAlchemy 2 Row access) now map to the API
behind them; a bad argument value (`'seaborn-white' is not a valid style`) is traced to the call
that received it; a symbol that still fails after its verdict was applied is re-retrieved with the
error message as a query, and those chunks go first because the diff stage only shows the top few.

### Protecting blocks that already work (`6_protect`)

Opt-in (`PipelineFlags.protect_working_blocks`, config `6_protect`, on by default in
`scripts/demo.py` and the web service). A `precheck` stage runs the ORIGINAL tutorial in its target
environment and in its old environment (on an older Python where the old release has no wheel for
3.10), and rewrite never edits a block that already runs. When the precheck ran, the change log also
says which blocks now print different output from what the original printed.

### Twenty real tutorials

Web pages (pandas, NumPy, SQLAlchemy, FastAPI/pydantic) and eight chapters of the 2016 *Python Data
Science Handbook* (NumPy, Matplotlib, scikit-learn) were fetched, converted to Markdown with a small
stdlib script and run through `5_loop` with `scripts/demo.py`. No hand-written references and no
assertions, so "passing" only means the block ran; this is a smoke test, not a score.

| | python blocks | run as-is | run after the pipeline |
|---|---|---|---|
| 20 real tutorials | 624 | 467 (75%) | 545 (87%) |

Largest gains: a NumPy page 76 to 111 of 117 blocks, a Matplotlib chapter 1 to 15 of 15 and another
0 to 10 of 10 (the old `seaborn-whitegrid` style names), a scikit-learn chapter 20 to 25 of 32, a
pandas add-row page 0 to 2 of 3. Output comparison against the original in its old environment: 58 of
58 comparable blocks matched on the pages where the old environment could be built, apart from one
`object`-dtype difference that the change log now flags.

What does not improve is mostly not version rot, and the change log says so per block ("Likely
cause: ..."): a snippet that uses `List` or `Alice` without defining it, imports of the author's own
project (`app.schemas`, `yourapplication`), a MySQL server, an intentional broadcasting error. Real
misses remain too: a FastAPI/pydantic docs page (`name = 'Jane Doe'` without an annotation) stays
at 0 of 7 because the error names no API the corpus can match.

## Reproducing the evaluation

```bash
uv run python -m trf.eval.validate_dataset                      # dev set (Docker)
uv run python -m trf.eval.validate_dataset --dataset-dir src/trf/eval/dataset_heldout
uv run python -m trf.eval.validate_dataset --dataset-dir src/trf/eval/dataset_libs
uv run python -m trf.eval.run_eval [--dataset-dir DIR --out runs/DIR]   # resumable; runs/<out>/results.md
uv run python scripts/check_case.py CASE... [--dataset-dir DIR]   # one case: old env / target env / reference
```

`--retry-errors` re-runs cases whose log recorded a crash, and a spent budget stops the eval
cleanly (see Spend cap). Every log records its own billed tokens.

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

## Spend cap

`TRF_MAX_SPEND_USD` (with `TRF_PRICE_IN_PER_M` and `TRF_PRICE_OUT_PER_M`, USD per million tokens;
use prices at or above the real ones) puts a hard budget on the OpenAI backend. Before every call
the backend assumes the worst case (a prompt at 2 characters per token and every allowed completion
token generated), refuses to send it if the running total in `TRF_SPEND_LEDGER`
(default `.trf_cache/spend.json`, shared by all processes) plus that worst case would pass 90% of
the cap, and raises `BudgetExceeded`, which no stage catches. `run_eval` then stops without logging
the case as a crash. Billed tokens are counted per run (`tokens` in each eval log, printed by
`demo.py`). Cached responses cost nothing.

## Adding a library

A library is one `register_library(LibraryConfig(...))` call in `src/trf/config.py`:

```python
register_library(LibraryConfig(
    name="numpy",                 # the import name
    pypi_name="numpy",            # what pip installs
    target_major=2,               # latest release within this major is the target
    bundled_imports=[],           # modules pip installs with it (not extra packages)
    changelog_sources=[...],      # raw .rst/.md release notes on GitHub, one per minor version
    migration_sources=[...],      # raw migration guides
))
```

Then: add the file-name pattern that carries each source's version to `_URL_VERSIONS` in
`rag/corpus.py`, the wheel/Python limits of old releases to `OLD_PYTHON_FOR` in `nodes/precheck.py`,
build the corpus (`uv run python -m trf.rag.corpus build NAME`), and write a few tutorials for
`eval/` (see `scripts/check_case.py`). Everything else (symbol extraction, retrieval, diff,
rewrite, sandbox, loop) is library-independent. The corpus is only as old as the sources you list:
a change that predates them (Matplotlib before 2.2, say) cannot be cited.

## Known limitations

- One model, one run per config, 33 evaluation tutorials, temperature 0: differences of a point or
  two are noise. A reasoning model on a small local GPU returned empty replies on every diff call,
  which is why the eval used a hosted model. Several fixes were found by running the dev and libs
  sets, so those numbers are optimistic; only the held-out set was not tuned on.
- A change can only be cited if it is in the release notes the corpus holds (the sources start at
  NumPy 1.17, scikit-learn 0.18, Matplotlib 2.2, SQLAlchemy 1.3, pandas 1.0, pydantic 1.x), and the
  corpus covers each project's own notes, not Stack Overflow answers or third-party docs.
- Old environments cannot be built for very old pins (NumPy 1.11 on a Python that has wheels for
  it, Matplotlib 1.5): the original-vs-updated output comparison is then skipped and a warning says
  so.
- `query_rewrite` is pattern matching on common Python errors. Errors that carry no symbol (a
  changed default producing different output, cascades from an earlier block) leave the block
  `unresolved` rather than guessed at. The loop's gain is measured in Results.
- The web service runs jobs in a worker pool (`TRF_API_WORKERS`, default 1) and keeps the last 100
  in `.trf_cache/jobs`; there is no authentication, so run it locally.


- Fenced blocks indented more than three spaces (e.g. inside list items) are treated as prose.
- Method-call symbol extraction uses a light, flow-insensitive type tracker, not real type inference.
- Blocks share one namespace, so a failing early block can make later blocks fail too; those are
  tagged "may be a downstream effect" in `last_error`, which is a hint, not a diagnosis.
- Prose is never edited, so prose that mentions old versions or APIs stays as written.
- Install-block updates are deterministic and only handle simple `pip install` lines.
- With a reasoning model on a small local GPU each LLM call takes tens of seconds; the diff stage is
  batched per library for this reason.

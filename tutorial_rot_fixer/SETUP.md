# Setup guide

Everything you need to get Tutorial Rot Fixer (TRF) running from a fresh checkout. The project
lives in the `tutorial_rot_fixer/` directory of the repository; run all commands from there.

## 1. Prerequisites

| Requirement | Why | Check |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python env + dependency management (installs Python 3.11+ for you) | `uv --version` |
| Docker (daemon running) | All tutorial code executes **only** in a Docker sandbox, never on the host | `docker info` |
| Internet access | Corpus fetch (GitHub raw), PyPI version lookup, dependency install during sandbox image builds | |
| An LLM (optional until Phase 3 runs) | Diff verdicts and rewrites. Any of: local Ollama, an OpenAI-compatible server, Anthropic | see section 5 |

Disk/RAM: the `rag` extra pulls in `torch` (a few GB on disk) and downloads two small models
(~220 MB) on first use. An NVIDIA GPU is not required for retrieval; it only matters if you run a
local LLM.

## 2. Install

```bash
cd tutorial_rot_fixer
uv sync                      # core deps + dev tools (pytest, ruff)
uv sync --extra rag          # retrieval stack: sentence-transformers, numpy
```

Verify:

```bash
uv run ruff check .
uv run pytest                # Docker-backed tests are skipped, not faked, if Docker is down
```

## 3. Build the sandbox image

The sandbox runner image is built automatically (and cached by requirement hash) the first time a
tutorial is executed. To build the plain smoke-test image yourself:

```bash
docker build -t trf-sandbox:latest src/trf/sandbox_runner
```

How execution is isolated: dependencies are installed at **image build time** (network on). Every
run then happens with `--network none`, a read-only filesystem, all capabilities dropped,
`no-new-privileges`, and CPU / memory / pid / wall-clock limits (`TRF_SANDBOX_*` variables below).

## 4. Build the retrieval corpus

```bash
uv run python -m trf.rag.corpus build            # pydantic + pandas
uv run python -m trf.rag.corpus build pandas     # one library
uv run python -m trf.rag.corpus build --refresh  # re-download sources
```

This fetches migration guides, changelogs and docs (raw Markdown/RST from GitHub), chunks them and
writes `.trf_cache/corpus/<library>.jsonl`. Embeddings are computed on first retrieval and cached in
`.trf_cache/dense/`. Check retrieval quality with:

```bash
uv run python scripts/retrieval_sanity.py
```

## 5. Configure the LLM (environment variables only)

Nothing is hardcoded: provider, model and credentials come from the environment. A node that does
not need the LLM never constructs it, so unit tests and the retrieval/sandbox stages run without
any of this.

| Variable | Meaning |
|---|---|
| `TRF_LLM_PROVIDER` | `ollama`, `openai` (any OpenAI-compatible server) or `anthropic` |
| `TRF_LLM_MODEL` | Model name for that provider |
| `TRF_LLM_API_KEY` | Required for `anthropic`; optional for others |
| `TRF_LLM_BASE_URL` | Override the endpoint (e.g. vLLM, OpenRouter, a remote Ollama) |
| `TRF_LLM_MAX_TOKENS` | Output budget (default 4096). **Reasoning models spend this on thinking too** |
| `TRF_LLM_NUM_CTX` | Ollama context window (its default is small; use 8192+) |
| `TRF_LLM_TIMEOUT_S` | Per-request timeout (default 900) |
| `TRF_LLM_TEMPERATURE` | Default 0 |
| `TRF_LLM_CACHE_DIR` | Cache responses on disk: identical calls are free, evals are resumable |

Examples (bash):

```bash
# Local Ollama (reasoning models like qwen3 need a large token budget)
export TRF_LLM_PROVIDER=ollama TRF_LLM_MODEL=qwen3-vl:8b
export TRF_LLM_NUM_CTX=8192 TRF_LLM_MAX_TOKENS=8000 TRF_LLM_CACHE_DIR=.trf_cache/llm

# Anthropic
export TRF_LLM_PROVIDER=anthropic TRF_LLM_MODEL=<model-id> TRF_LLM_API_KEY=<key>

# Any OpenAI-compatible endpoint
export TRF_LLM_PROVIDER=openai TRF_LLM_MODEL=<model> TRF_LLM_BASE_URL=http://host:8000/v1
```

PowerShell: `$env:TRF_LLM_PROVIDER = "ollama"` (and so on).

### Pipeline and sandbox switches

| Variable | Default | Meaning |
|---|---|---|
| `TRF_USE_BM25`, `TRF_USE_DENSE`, `TRF_USE_RERANK`, `TRF_USE_VERSION_BOOST`, `TRF_USE_SYMBOL_INDEX`, `TRF_USE_SANDBOX_LOOP` | `1` | Toggle each stage (the eval ablates these) |
| `TRF_MAX_LOOPS` | `4` | Fix-loop cap before blocks are marked `unresolved` |
| `TRF_CACHE_DIR` | `.trf_cache` | Corpus / HTTP / embedding cache location |
| `TRF_SANDBOX_IMAGE`, `TRF_SANDBOX_MEMORY`, `TRF_SANDBOX_CPUS`, `TRF_SANDBOX_PIDS`, `TRF_SANDBOX_TIMEOUT_S`, `TRF_SANDBOX_PYTHON` | `trf-sandbox:latest`, `512m`, `1`, `128`, `60`, `3.12` | Sandbox limits and interpreter |

## 6. Common tasks

```bash
uv run pytest                                   # full test suite
uv run python -m trf.eval.baseline              # run ORIGINAL tutorials in the target env (how broken?)
uv run python -m trf.eval.baseline --mode old   # control: original pins; should be all green
uv run python scripts/retrieval_sanity.py       # retrieval smoke test with the real models
```

## 7. Troubleshooting

- **`failed to connect to the docker API`**: start Docker Desktop (or the daemon). Docker-backed
  tests skip while it is down; the sandbox node raises rather than pretending code passed.
- **`UnicodeDecodeError` from a subprocess on Windows**: fixed in the sandbox by forcing UTF-8;
  if you add new subprocess calls, pass `encoding="utf-8", errors="replace"`.
- **`empty reply: the token budget was probably spent on reasoning`**: raise `TRF_LLM_MAX_TOKENS`.
  Reasoning models (e.g. qwen3) always think and ignore `think: false`.
- **Ollama truncates long prompts**: set `TRF_LLM_NUM_CTX` (8192 or more).
- **Dense retrieval downloads models on first run**: `BAAI/bge-small-en-v1.5` and
  `cross-encoder/ms-marco-MiniLM-L-6-v2`; they are cached by Hugging Face afterwards.
- **`ruff format` rewrites the dataset tutorials**: ruff formats Python inside Markdown. The dataset
  directory is excluded in `pyproject.toml`; keep it that way, tutorials are test data.
- **Old pandas fails with numpy errors in control runs**: pandas 1.x is binary-incompatible with
  numpy 2; the baseline's `--mode old` pins `numpy<2` and Python 3.10.

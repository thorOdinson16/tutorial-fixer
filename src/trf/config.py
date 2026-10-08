"""Environment-driven settings and the pluggable library registry."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from trf.pypi import fetch_releases, latest

ENV_PREFIX = "TRF_"


def load_dotenv(path: Path | str = ".env") -> list[str]:
    """Read KEY=VALUE lines from `path` into os.environ, never overriding a variable already set.

    Returns the names it set. A missing file is fine. Only `TRF_*` and the standard provider keys
    are accepted, so a stray `.env` cannot change unrelated environment such as PATH.
    """
    f = Path(path)
    if not f.is_file():
        return []
    set_names: list[str] = []
    for raw in f.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key.startswith(ENV_PREFIX) or key in os.environ:
            continue
        os.environ[key] = value
        set_names.append(key)
    return set_names


if not os.environ.get("TRF_NO_DOTENV"):
    load_dotenv()


class ConfigError(RuntimeError):
    pass


def _opt_float(name: str) -> float | None:
    raw = os.environ.get(ENV_PREFIX + name)
    return float(raw) if raw not in (None, "") else None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(ENV_PREFIX + name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class PipelineFlags(BaseModel):
    """Per-stage toggles so the eval can ablate each stage."""

    use_bm25: bool = True
    use_dense: bool = True
    use_rerank: bool = True
    use_version_boost: bool = True
    use_symbol_index: bool = True
    use_sandbox_loop: bool = True
    protect_working_blocks: bool = False  # opt-in: never edit a block that already works

    @property
    def retrieval_enabled(self) -> bool:
        """False for the 'LLM only' baseline: nothing to cite, so no diff stage."""
        return self.use_bm25 or self.use_dense or self.use_symbol_index

    @classmethod
    def from_env(cls) -> PipelineFlags:
        return cls(**{n: _env_bool(n.upper(), f.default) for n, f in cls.model_fields.items()})


class LLMSettings(BaseModel):
    """Provider, model and credentials come from env only. No defaults, nothing hardcoded."""

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_s: float = 900.0
    num_ctx: int | None = None  # ollama: context window (its default is small)
    cache_dir: str | None = None  # on-disk response cache (resumable, reproducible evals)
    max_spend_usd: float | None = None  # hard budget; needs both prices below
    price_in_per_m: float | None = None  # USD per million prompt tokens
    price_out_per_m: float | None = None  # USD per million completion tokens (incl. reasoning)
    spend_ledger: str = ".trf_cache/spend.json"  # running total, shared by every process

    @classmethod
    def from_env(cls) -> LLMSettings:
        provider = os.environ.get(ENV_PREFIX + "LLM_PROVIDER")
        model = os.environ.get(ENV_PREFIX + "LLM_MODEL")
        if not provider or not model:
            raise ConfigError(
                f"Set {ENV_PREFIX}LLM_PROVIDER and {ENV_PREFIX}LLM_MODEL "
                f"(and {ENV_PREFIX}LLM_API_KEY / {ENV_PREFIX}LLM_BASE_URL as needed)."
            )
        return cls(
            provider=provider,
            model=model,
            api_key=os.environ.get(ENV_PREFIX + "LLM_API_KEY"),
            base_url=os.environ.get(ENV_PREFIX + "LLM_BASE_URL"),
            temperature=float(os.environ.get(ENV_PREFIX + "LLM_TEMPERATURE", "0")),
            max_tokens=int(os.environ.get(ENV_PREFIX + "LLM_MAX_TOKENS", "4096")),
            timeout_s=float(os.environ.get(ENV_PREFIX + "LLM_TIMEOUT_S", "900")),
            num_ctx=int(os.environ[ENV_PREFIX + "LLM_NUM_CTX"])
            if os.environ.get(ENV_PREFIX + "LLM_NUM_CTX")
            else None,
            cache_dir=os.environ.get(ENV_PREFIX + "LLM_CACHE_DIR") or None,
            max_spend_usd=_opt_float("MAX_SPEND_USD"),
            price_in_per_m=_opt_float("PRICE_IN_PER_M"),
            price_out_per_m=_opt_float("PRICE_OUT_PER_M"),
            spend_ledger=os.environ.get(ENV_PREFIX + "SPEND_LEDGER", ".trf_cache/spend.json"),
        )


class SandboxSettings(BaseModel):
    image: str = "trf-sandbox:latest"
    memory: str = "512m"
    cpus: float = 1.0
    pids_limit: int = 128
    timeout_s: int = 60
    python_version: str = "3.12"

    @classmethod
    def from_env(cls) -> SandboxSettings:
        d = cls()
        return cls(
            image=os.environ.get(ENV_PREFIX + "SANDBOX_IMAGE", d.image),
            memory=os.environ.get(ENV_PREFIX + "SANDBOX_MEMORY", d.memory),
            cpus=float(os.environ.get(ENV_PREFIX + "SANDBOX_CPUS", d.cpus)),
            pids_limit=int(os.environ.get(ENV_PREFIX + "SANDBOX_PIDS", d.pids_limit)),
            timeout_s=int(os.environ.get(ENV_PREFIX + "SANDBOX_TIMEOUT_S", d.timeout_s)),
            python_version=os.environ.get(ENV_PREFIX + "SANDBOX_PYTHON", d.python_version),
        )


class Settings(BaseModel):
    flags: PipelineFlags = Field(default_factory=PipelineFlags)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    max_loops: int = 4

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            flags=PipelineFlags.from_env(),
            sandbox=SandboxSettings.from_env(),
            max_loops=int(os.environ.get(ENV_PREFIX + "MAX_LOOPS", "4")),
        )


# --- Library registry -------------------------------------------------------

# (pypi_name, target_major) -> version string
VersionResolver = Callable[[str, int | None], str]


def pypi_latest(pypi_name: str, major: int | None = None) -> str:
    """Default resolver: latest stable release on PyPI, within `major` if given."""
    v = latest(fetch_releases(pypi_name), major=major)
    if v is None:
        raise ConfigError(f"No release of {pypi_name} found (major={major})")
    return v


class LibraryConfig(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    name: str  # import name, e.g. "pydantic"
    pypi_name: str
    docs_sources: list[str] = Field(default_factory=list)
    changelog_sources: list[str] = Field(default_factory=list)
    migration_sources: list[str] = Field(default_factory=list)
    target_major: int | None = None  # the migration's destination major (v1: pandas 2, pydantic 2)
    bundled_imports: list[str] = Field(default_factory=list)  # modules pip installs with it
    version_resolver: VersionResolver = pypi_latest

    def resolve_target_version(self, pin: str | None = None) -> str:
        return pin or self.version_resolver(self.pypi_name, self.target_major)


LIBRARIES: dict[str, LibraryConfig] = {}


def register_library(cfg: LibraryConfig) -> LibraryConfig:
    LIBRARIES[cfg.name] = cfg
    return cfg


def get_library(name: str) -> LibraryConfig:
    try:
        return LIBRARIES[name]
    except KeyError:
        raise ConfigError(
            f"Unsupported library {name!r}; registered: {sorted(LIBRARIES)}"
        ) from None


_GH = "https://raw.githubusercontent.com"
_PD_DOCS = f"{_GH}/pydantic/pydantic/main/docs"
_PANDAS_WN = f"{_GH}/pandas-dev/pandas/main/doc/source/whatsnew"

# Sources are raw markdown/RST (format inferred from the extension) so chunking is clean.
register_library(
    LibraryConfig(
        name="pydantic",
        pypi_name="pydantic",
        target_major=2,
        bundled_imports=["typing_extensions", "annotated_types", "pydantic_core"],
        docs_sources=[
            f"{_PD_DOCS}/concepts/{p}.md"
            for p in ("models", "validators", "fields", "serialization", "config")
        ],
        changelog_sources=[f"{_GH}/pydantic/pydantic/main/HISTORY.md"],
        migration_sources=[f"{_PD_DOCS}/migration.md"],
    )
)
register_library(
    LibraryConfig(
        name="pandas",
        pypi_name="pandas",
        target_major=2,
        bundled_imports=["numpy", "pytz", "dateutil"],
        docs_sources=[],
        # minor-release notes carry the deprecations/removals; patch notes are bug fixes
        changelog_sources=[
            f"{_PANDAS_WN}/v{v}.rst"
            for v in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0")
            + ("2.1.0", "2.2.0", "2.3.0")
        ],
        migration_sources=[f"{_PANDAS_WN}/v2.0.0.rst"],
    )
)


def get_cache_dir() -> Path:
    """Corpus, HTTP and embedding caches live here (override with TRF_CACHE_DIR)."""
    return Path(os.environ.get(ENV_PREFIX + "CACHE_DIR", ".trf_cache"))

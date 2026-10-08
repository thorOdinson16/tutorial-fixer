"""Find the blocks that already work, so the pipeline can leave them alone.

Opt-in (`PipelineFlags.protect_working_blocks`). Before any retrieval, the ORIGINAL tutorial is run
twice in the sandbox: in the target environment, and in its old environment (the pinned or
date-inferred versions, on a Python those versions have wheels for). A block is *protected* when it
runs in the target environment and, where it also ran in the old one, prints the same thing. The
rewrite stage then never edits a protected block, which removes edits that only chase deprecations
(`applymap` -> `map`) and edits that change behaviour that was fine.

A block that runs in both environments but prints something different is NOT protected, and a
warning says so: that is a silent behaviour change the diff stage should look at. If the old
environment cannot be built (no wheels, unknown old version) the comparison is skipped and a block
is protected on "runs in the target environment" alone.
"""

from __future__ import annotations

from collections.abc import Callable

from packaging.version import InvalidVersion, Version

from trf.config import PipelineFlags, SandboxSettings
from trf.nodes.sandbox import ensure_env_image, make_sandbox
from trf.state import GraphState

OLD_EXTRA_REQS = ["numpy<2"]  # pandas 1.x is binary-incompatible with numpy 2
DEFAULT_OLD_PYTHON = "3.10"  # old pandas/pydantic wheels exist for 3.10, not 3.12

# (pandas version below which, python the old env needs). Wheels do not exist for newer Pythons.
PANDAS_PYTHON = [("1.1.4", "3.8"), ("1.3.4", "3.9")]

Runner = Callable[[GraphState, str, SandboxSettings], GraphState]


def old_python(old_versions: dict[str, str]) -> str:
    """The Python an old environment needs: the oldest any pinned library requires."""
    v = old_versions.get("pandas")
    if v:
        try:
            for below, py in PANDAS_PYTHON:
                if Version(v) < Version(below):
                    return py
        except InvalidVersion:
            pass
    return DEFAULT_OLD_PYTHON


def run_in_env(s: GraphState, mode: str, settings: SandboxSettings) -> GraphState:
    """Execute an ingested/extracted/resolved state in the target env or the old-pins env."""
    builder = ensure_env_image
    if mode == "old":
        missing = [lib for lib in s.libraries if lib not in s.old_versions]
        if missing:
            raise SystemExit(f"no old version known for {missing}; cannot run control")
        s = s.model_copy(update={"target_versions": dict(s.old_versions)})
        settings = settings.model_copy(update={"python_version": old_python(s.old_versions)})

        def builder(reqs, optional, st):
            return ensure_env_image([*reqs, *OLD_EXTRA_REQS], optional, st)

    return s.model_copy(update=make_sandbox(env_builder=builder, settings=settings)(s))


def compare(target: GraphState, old: GraphState | None) -> tuple[list[str], list[str]]:
    """(protected block ids, ids that run in both envs but print different things)."""
    tgt = {r.block_id: r for r in target.run_results}
    prev = {r.block_id: r for r in old.run_results} if old else {}
    protected: list[str] = []
    drift: list[str] = []
    for b in target.blocks:
        if b.lang != "python":
            continue
        t, o = tgt.get(b.id), prev.get(b.id)
        if t is None or not t.ok:
            continue
        if o is not None and o.ok and o.stdout.strip() != t.stdout.strip():
            drift.append(b.id)
            continue
        protected.append(b.id)
    return protected, drift


def make_precheck(
    flags: PipelineFlags | None = None,
    runner: Runner = run_in_env,
    settings: SandboxSettings | None = None,
) -> Callable[[GraphState], dict]:
    def precheck(state: GraphState) -> dict:
        f = flags or PipelineFlags()
        if not f.protect_working_blocks or not any(b.lang == "python" for b in state.blocks):
            return {}
        st = settings or SandboxSettings.from_env()
        warnings = list(state.warnings)
        target = runner(state, "target", st)
        old: GraphState | None = None
        try:
            old = runner(state, "old", st)
        except SystemExit as e:  # old version unknown
            warnings.append(f"precheck: old environment not run ({e})")
        protected, drift = compare(target, old)
        if old is not None and not any(r.ok for r in old.run_results):
            warnings.append("precheck: the original does not run in its old environment either")
        if drift:
            warnings.append(
                f"precheck: {', '.join(drift)} run on both versions but print different output "
                "(not protected; a behaviour change)"
            )
        return {"protected_blocks": protected, "warnings": warnings}

    return precheck

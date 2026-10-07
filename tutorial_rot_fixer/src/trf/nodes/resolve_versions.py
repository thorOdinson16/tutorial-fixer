"""Old version (from the tutorial) vs target version (PyPI latest in the target major, or user pin).

Old-version evidence, strongest first:
  1. exact pin in the tutorial (`pandas==1.3.5`)
  2. range pin (`pydantic<2`) -> highest release satisfying it
  3. tutorial date -> highest release published on or before that date
If none applies, the library is simply absent from `old_versions` (unknown, not guessed).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time

from trf.config import LIBRARIES, LibraryConfig
from trf.nodes.ingest import _norm
from trf.pypi import Release, fetch_releases, latest, latest_before
from trf.state import GraphState

Releases = Callable[[str], tuple[Release, ...] | list[Release]]


def make_resolve_versions(
    releases: Releases = fetch_releases,
    registry: dict[str, LibraryConfig] | None = None,
) -> Callable[[GraphState], dict]:
    """Build the node; `releases` and `registry` are injectable so tests need no network."""

    def resolve_versions(state: GraphState) -> dict:
        reg = registry if registry is not None else LIBRARIES
        old: dict[str, str] = {}
        target: dict[str, str] = {}
        for name in state.libraries:
            cfg = reg[name]
            spec = state.pins.get(_norm(cfg.pypi_name))
            old_v: str | None = None
            if spec and spec.startswith("==") and "*" not in spec:
                old_v = spec[2:]
            elif spec:
                old_v = latest(releases(cfg.pypi_name), spec=spec)
            elif state.tutorial_date:
                when = datetime.combine(state.tutorial_date, time.max, tzinfo=UTC)
                old_v = latest_before(releases(cfg.pypi_name), when)
            if old_v:
                old[name] = old_v
            target[name] = cfg.resolve_target_version(state.user_target_pins.get(name))
        symbols = [s.model_copy(update={"old_version": old.get(s.library)}) for s in state.symbols]
        return {"old_versions": old, "target_versions": target, "symbols": symbols}

    return resolve_versions


resolve_versions = make_resolve_versions()

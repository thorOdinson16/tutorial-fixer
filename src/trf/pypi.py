"""PyPI release metadata: used to resolve target versions and to date-infer old versions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class Release:
    version: str
    uploaded: datetime


@lru_cache(maxsize=64)
def fetch_releases(pypi_name: str) -> tuple[Release, ...]:
    """All non-yanked, non-prerelease releases, oldest first."""
    r = httpx.get(f"https://pypi.org/pypi/{pypi_name}/json", timeout=20)
    r.raise_for_status()
    out: list[Release] = []
    for ver, files in r.json()["releases"].items():
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        live = [f for f in files if not f.get("yanked")]
        if v.is_prerelease or v.is_devrelease or not live:
            continue
        uploaded = min(
            datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00")) for f in live
        )
        out.append(Release(ver, uploaded.astimezone(UTC)))
    return tuple(sorted(out, key=lambda x: Version(x.version)))


def latest(releases, *, major: int | None = None, spec: str | None = None) -> str | None:
    """Highest release, optionally restricted to a major version and/or a specifier like '<2'."""
    try:
        ss = SpecifierSet(spec) if spec else None
    except InvalidSpecifier:
        return None
    cands = [
        r.version
        for r in releases
        if (major is None or Version(r.version).major == major)
        and (ss is None or Version(r.version) in ss)
    ]
    return cands[-1] if cands else None


def latest_before(releases, when: datetime) -> str | None:
    """Highest release uploaded on or before `when`."""
    cands = [r.version for r in releases if r.uploaded <= when]
    return cands[-1] if cands else None

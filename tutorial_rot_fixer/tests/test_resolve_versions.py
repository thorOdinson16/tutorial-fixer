from datetime import UTC, date, datetime

from trf.config import LibraryConfig
from trf.nodes.resolve_versions import make_resolve_versions
from trf.pypi import Release, latest, latest_before
from trf.state import GraphState, Symbol

REL = tuple(
    Release(v, datetime(y, m, 1, tzinfo=UTC))
    for v, y, m in [("1.0.0", 2020, 1), ("1.10.0", 2021, 6), ("2.0.0", 2023, 7), ("2.5.0", 2024, 5)]
)
CFG = {
    "lib": LibraryConfig(
        name="lib", pypi_name="lib", target_major=2, version_resolver=lambda n, m: f"resolved-{m}"
    )
}
node = make_resolve_versions(releases=lambda name: REL, registry=CFG)


def run(**kw):
    s = GraphState(libraries=["lib"], symbols=[Symbol(name="lib.f", library="lib")], **kw)
    return node(s)


def test_exact_pin_wins():
    out = run(pins={"lib": "==1.0.0"}, tutorial_date=date(2024, 1, 1))
    assert out["old_versions"] == {"lib": "1.0.0"}
    assert out["symbols"][0].old_version == "1.0.0"


def test_range_pin():
    assert run(pins={"lib": "<2"})["old_versions"] == {"lib": "1.10.0"}


def test_date_inference():
    assert run(tutorial_date=date(2022, 1, 1))["old_versions"] == {"lib": "1.10.0"}
    assert run(tutorial_date=date(2019, 1, 1))["old_versions"] == {}  # before first release


def test_unknown_stays_unknown():
    assert run()["old_versions"] == {}


def test_target_resolution_and_user_pin():
    assert run()["target_versions"] == {"lib": "resolved-2"}
    assert run(user_target_pins={"lib": "2.1.0"})["target_versions"] == {"lib": "2.1.0"}


def test_pypi_helpers():
    assert latest(REL) == "2.5.0"
    assert latest(REL, major=1) == "1.10.0"
    assert latest(REL, spec="<2") == "1.10.0"  # numeric, not lexicographic
    assert latest(REL, major=9) is None
    assert latest(REL, spec="garbage") is None
    assert latest_before(REL, datetime(2000, 1, 1, tzinfo=UTC)) is None

import pytest

from trf.config import ConfigError, PipelineFlags, get_library


def test_registry_has_v1_libraries():
    assert get_library("pydantic").pypi_name == "pydantic"
    assert get_library("pandas").migration_sources


def test_unknown_library():
    with pytest.raises(ConfigError):
        get_library("nope")


def test_pin_bypasses_resolver():
    cfg = get_library("pydantic").model_copy(update={"version_resolver": lambda n, m: "9.9.9"})
    assert cfg.resolve_target_version() == "9.9.9"
    assert cfg.resolve_target_version("2.7.0") == "2.7.0"


def test_flags_from_env(monkeypatch):
    monkeypatch.setenv("TRF_USE_BM25", "0")
    f = PipelineFlags.from_env()
    assert f.use_bm25 is False and f.use_dense is True

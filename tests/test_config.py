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


def test_load_dotenv_reads_trf_vars_without_overriding_the_environment(tmp_path, monkeypatch):
    from trf.config import load_dotenv

    f = tmp_path / ".env"
    f.write_text(
        "# comment\nTRF_LLM_PROVIDER=openai\nexport TRF_LLM_MODEL='some-model'\n"
        'TRF_LLM_API_KEY="k-123"\nPATH=/evil\nnot a line\nTRF_LLM_TEMPERATURE=0.5\n',
        encoding="utf-8",
    )
    for name in ("TRF_LLM_PROVIDER", "TRF_LLM_MODEL", "TRF_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRF_LLM_TEMPERATURE", "0.0")  # already set: .env must not win
    monkeypatch.setenv("PATH", "/safe")

    names = load_dotenv(f)

    assert sorted(names) == ["TRF_LLM_API_KEY", "TRF_LLM_MODEL", "TRF_LLM_PROVIDER"]
    import os

    assert os.environ["TRF_LLM_MODEL"] == "some-model"
    assert os.environ["TRF_LLM_API_KEY"] == "k-123"
    assert os.environ["TRF_LLM_TEMPERATURE"] == "0.0"
    assert os.environ["PATH"] == "/safe"
    for name in names:  # monkeypatch cannot undo what it did not set
        os.environ.pop(name, None)


def test_load_dotenv_missing_file_is_fine(tmp_path):
    from trf.config import load_dotenv

    assert load_dotenv(tmp_path / "nope.env") == []

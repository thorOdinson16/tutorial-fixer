from pathlib import Path

from trf.api import serve
from trf.config import LIBRARIES


def test_missing_corpora_lists_libraries_without_a_corpus_file(tmp_path: Path):
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "pandas.jsonl").write_text("", encoding="utf-8")
    missing = serve.missing_corpora(tmp_path)
    assert "pandas" not in missing
    assert set(missing) == set(LIBRARIES) - {"pandas"}


def test_check_reports_docker_and_model_problems(monkeypatch):
    monkeypatch.setattr(serve, "docker_ready", lambda: False)
    for name in ("TRF_LLM_PROVIDER", "TRF_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    problems = serve.check()
    assert any("Docker" in p for p in problems)
    assert any("model" in p.lower() for p in problems)


def test_check_is_clean_when_everything_is_configured(monkeypatch):
    monkeypatch.setattr(serve, "docker_ready", lambda: True)
    monkeypatch.setenv("TRF_LLM_PROVIDER", "openai")
    monkeypatch.setenv("TRF_LLM_MODEL", "m")
    assert serve.check() == []

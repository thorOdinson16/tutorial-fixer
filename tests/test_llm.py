import pytest
from pydantic import BaseModel

from trf.config import ConfigError
from trf.llm import LLM, LLMError


class Answer(BaseModel):
    verdict: str
    confidence: float


class FakeBackend:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, messages, *, json_schema=None):
        self.calls.append((system, list(messages)))
        self.schemas = getattr(self, "schemas", []) + [json_schema]
        return self.replies.pop(0)


def test_structured_ok_with_fence():
    fb = FakeBackend(['```json\n{"verdict": "renamed", "confidence": 0.8}\n```'])
    out = LLM(fb).structured("q", Answer)
    assert out == Answer(verdict="renamed", confidence=0.8)
    assert "JSON Schema" in fb.calls[0][0]


def test_structured_retries_with_error_feedback():
    fb = FakeBackend(['{"verdict": "x"}', '{"verdict": "x", "confidence": 1}'])
    out = LLM(fb).structured("q", Answer)
    assert out.confidence == 1
    assert "invalid" in fb.calls[1][1][-1]["content"]


def test_structured_gives_up():
    fb = FakeBackend(["nope"] * 3)
    with pytest.raises(LLMError):
        LLM(fb).structured("q", Answer, max_retries=2)


def test_from_env_requires_config(monkeypatch):
    monkeypatch.delenv("TRF_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TRF_LLM_MODEL", raising=False)
    with pytest.raises(ConfigError):
        LLM.from_env()


def test_from_env_unknown_provider(monkeypatch):
    monkeypatch.setenv("TRF_LLM_PROVIDER", "bogus")
    monkeypatch.setenv("TRF_LLM_MODEL", "m")
    with pytest.raises(ConfigError):
        LLM.from_env()


def test_from_env_builds_backend(monkeypatch):
    monkeypatch.setenv("TRF_LLM_PROVIDER", "openai")
    monkeypatch.setenv("TRF_LLM_MODEL", "m")
    monkeypatch.setenv("TRF_LLM_BASE_URL", "http://localhost:11434/v1")
    llm = LLM.from_env()
    assert llm.backend.url == "http://localhost:11434/v1/chat/completions"


def test_structured_passes_json_schema_to_backend():
    fb = FakeBackend(['{"verdict": "x", "confidence": 1}'])
    LLM(fb).structured("q", Answer)
    assert fb.schemas[0]["properties"]["verdict"]["type"] == "string"


def test_caching_backend_roundtrip(tmp_path):
    from trf.llm import CachingBackend

    inner = FakeBackend(["first", "second"])
    c = CachingBackend(inner, tmp_path, salt="m")
    assert c.complete("s", [{"role": "user", "content": "q"}]) == "first"
    assert c.complete("s", [{"role": "user", "content": "q"}]) == "first"  # served from disk
    assert c.complete("s", [{"role": "user", "content": "other"}]) == "second"
    assert (c.hits, c.misses) == (1, 2)
    assert (
        CachingBackend(FakeBackend([]), tmp_path, salt="m").complete(
            "s", [{"role": "user", "content": "q"}]
        )
        == "first"
    )  # persists across instances
    assert (
        CachingBackend(FakeBackend(["x"]), tmp_path, salt="other-model").complete(
            "s", [{"role": "user", "content": "q"}]
        )
        == "x"
    )  # salt separates models


def test_ollama_backend_request_shape(monkeypatch):
    import httpx

    from trf.config import LLMSettings
    from trf.llm import OllamaBackend

    seen = {}

    def fake_post(url, **kw):
        seen.update(url=url, **kw)
        return httpx.Response(
            200,
            json={"message": {"content": "{}", "thinking": "..."}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    s = LLMSettings(provider="ollama", model="m", num_ctx=8192, max_tokens=999)
    assert (
        OllamaBackend(s).complete("sys", [{"role": "user", "content": "q"}], json_schema={"a": 1})
        == "{}"
    )
    assert seen["url"] == "http://localhost:11434/api/chat"
    assert seen["json"]["format"] == {"a": 1}
    assert seen["json"]["options"] == {"temperature": 0.0, "num_predict": 999, "num_ctx": 8192}
    assert seen["json"]["messages"][0] == {"role": "system", "content": "sys"}


def test_ollama_empty_reply_is_an_error(monkeypatch):
    import httpx

    from trf.config import LLMSettings
    from trf.llm import OllamaBackend

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: httpx.Response(
            200,
            json={"message": {"content": "", "thinking": "x"}},
            request=httpx.Request("POST", url),
        ),
    )
    with pytest.raises(LLMError, match="empty reply"):
        OllamaBackend(LLMSettings(provider="ollama", model="m")).complete(None, [])

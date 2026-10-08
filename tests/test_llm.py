import pytest
from pydantic import BaseModel

from trf.config import ConfigError, LLMSettings
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


class _Resp:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._p = status, text, payload

    @property
    def is_error(self):
        return self.status_code >= 400

    def json(self):
        return self._p


def _openai(monkeypatch, replies, base_url=None):
    from trf.llm import OpenAIBackend

    sent = []

    def post(url, headers, timeout, json):
        sent.append(json)
        return replies.pop(0)

    monkeypatch.setattr("trf.llm.httpx.post", post)
    s = LLMSettings(provider="openai", model="m", api_key="k", base_url=base_url, max_tokens=77)
    return OpenAIBackend(s), sent


OK = _Resp(200, payload={"choices": [{"message": {"content": "hi"}}]})


def test_openai_official_endpoint_uses_max_completion_tokens(monkeypatch):
    b, sent = _openai(monkeypatch, [OK])
    assert b.complete("sys", [{"role": "user", "content": "x"}]) == "hi"
    assert sent[0]["max_completion_tokens"] == 77 and "max_tokens" not in sent[0]


def test_openai_compatible_server_keeps_max_tokens(monkeypatch):
    b, sent = _openai(monkeypatch, [OK], base_url="http://localhost:8000/v1")
    b.complete(None, [])
    assert sent[0]["max_tokens"] == 77 and "max_completion_tokens" not in sent[0]


def test_openai_switches_param_and_drops_unsupported_temperature(monkeypatch):
    replies = [
        _Resp(400, "Unsupported parameter: 'max_tokens' ... Use 'max_completion_tokens'"),
        _Resp(400, "Unsupported value: 'temperature' does not support 0"),
        OK,
    ]
    b, sent = _openai(monkeypatch, replies, base_url="http://localhost:8000/v1")
    assert b.complete(None, []) == "hi"
    assert "max_completion_tokens" in sent[1] and "temperature" in sent[1]
    assert "temperature" not in sent[2]
    # the fixes are remembered: the next call goes out right the first time
    monkeypatch.setattr("trf.llm.httpx.post", lambda *a, **k: (sent.append(k["json"]), OK)[1])
    b.complete(None, [])
    assert "max_completion_tokens" in sent[3] and "temperature" not in sent[3]


def test_openai_other_errors_still_raise(monkeypatch):
    import pytest

    from trf.llm import LLMError

    b, _ = _openai(monkeypatch, [_Resp(401, "bad key")])
    with pytest.raises(LLMError, match="401"):
        b.complete(None, [])


def test_openai_retries_network_blips_and_server_errors(monkeypatch):
    import httpx

    monkeypatch.setattr("trf.llm.time.sleep", lambda s: None)
    calls = []

    def post(url, headers, timeout, json):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("getaddrinfo failed")
        if len(calls) == 2:
            return _Resp(503, "overloaded")
        return OK

    monkeypatch.setattr("trf.llm.httpx.post", post)
    from trf.llm import OpenAIBackend

    b = OpenAIBackend(LLMSettings(provider="openai", model="m", api_key="k"))
    assert b.complete(None, []) == "hi"
    assert len(calls) == 3


def test_openai_gives_up_after_repeated_network_errors(monkeypatch):
    import httpx

    monkeypatch.setattr("trf.llm.time.sleep", lambda s: None)

    def post(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("trf.llm.httpx.post", post)
    from trf.llm import OpenAIBackend

    b = OpenAIBackend(LLMSettings(provider="openai", model="m", api_key="k"))
    with pytest.raises(LLMError, match="network error"):
        b.complete(None, [])


def test_openai_counts_billed_tokens(monkeypatch):
    reply = _Resp(
        200,
        payload={
            "choices": [{"message": {"content": "hi"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "completion_tokens_details": {"reasoning_tokens": 25},
            },
        },
    )
    b, _ = _openai(monkeypatch, [reply, reply])
    llm = LLM(b)
    llm.complete("x")
    llm.complete("y")
    assert llm.usage() == {
        "api_calls": 2,
        "prompt_tokens": 200,
        "completion_tokens": 80,
        "reasoning_tokens": 50,
    }
    assert LLM(object()).usage() == {}  # a backend that reports nothing


def _budget_backend(monkeypatch, tmp_path, cap, replies, max_tokens=100):
    from trf.llm import OpenAIBackend

    sent = []

    def post(url, headers, timeout, json):
        sent.append(json)
        return replies.pop(0)

    monkeypatch.setattr("trf.llm.httpx.post", post)
    s = LLMSettings(
        provider="openai",
        model="m",
        api_key="k",
        max_tokens=max_tokens,
        max_spend_usd=cap,
        price_in_per_m=1000.0,  # deliberately huge so tiny token counts matter
        price_out_per_m=2000.0,
        spend_ledger=str(tmp_path / "spend.json"),
    )
    return OpenAIBackend(s), sent


def _usage_reply(p, c):
    return _Resp(
        200,
        payload={
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": p, "completion_tokens": c},
        },
    )


def test_spend_cap_stops_calls_before_they_can_cross_it(monkeypatch, tmp_path):
    import json

    from trf.llm import BudgetExceeded

    # worst case for one call here is ~$0.20 (100 tokens at $2000/M); cap 0.40 -> 90% = 0.36
    b, sent = _budget_backend(
        monkeypatch, tmp_path, 0.40, [_usage_reply(10, 50), _usage_reply(10, 50)]
    )
    b.complete(None, [{"role": "user", "content": "x"}])  # actual cost 0.11
    b.complete(None, [{"role": "user", "content": "x"}])  # total 0.22; next worst case 0.20 > 0.36?
    spent = json.loads((tmp_path / "spend.json").read_text())["usd"]
    assert abs(spent - 0.22) < 1e-9
    import pytest

    with pytest.raises(BudgetExceeded):
        b.complete(None, [{"role": "user", "content": "x"}])
    assert len(sent) == 2  # the refused call never reached the network


def test_budget_exceeded_is_not_swallowed_as_an_llm_error():
    from trf.llm import BudgetExceeded

    assert not issubclass(BudgetExceeded, LLMError)


def test_spend_cap_requires_prices():
    from trf.llm import OpenAIBackend

    with pytest.raises(ConfigError, match="PRICE_IN_PER_M"):
        OpenAIBackend(LLMSettings(provider="openai", model="m", max_spend_usd=1.0))


def test_ledger_total_survives_a_new_process(monkeypatch, tmp_path):
    b, _ = _budget_backend(monkeypatch, tmp_path, 10.0, [_usage_reply(10, 50)])
    b.complete(None, [{"role": "user", "content": "x"}])
    again, _ = _budget_backend(monkeypatch, tmp_path, 10.0, [])
    assert abs(again.ledger.total() - 0.11) < 1e-9

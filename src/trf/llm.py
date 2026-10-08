"""Single provider-agnostic LLM wrapper. All LLM access in the project goes through here.

Providers (TRF_LLM_PROVIDER): `anthropic`, `openai` (any OpenAI-compatible server via
TRF_LLM_BASE_URL) and `ollama` (native API: schema-constrained JSON and a configurable context
window). Nothing is hardcoded: provider, model and credentials come from the environment.
Set TRF_LLM_CACHE_DIR to cache responses on disk (identical calls are free and reproducible).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from trf.config import ConfigError, LLMSettings

T = TypeVar("T", bound=BaseModel)

Message = dict[str, str]  # {"role": "user"|"assistant", "content": "..."}


class LLMError(RuntimeError):
    pass


class Backend(Protocol):
    def complete(
        self, system: str | None, messages: list[Message], *, json_schema: dict | None = None
    ) -> str: ...


RETRY_DELAYS = (2, 5, 15, 45, 90)  # seconds between attempts on transient failures


class OpenAIBackend:
    """OpenAI-style /chat/completions. Also covers vLLM, OpenRouter, etc. via base_url.

    api.openai.com's newer models reject `max_tokens` (they want `max_completion_tokens`) and some
    reject a custom temperature; compatible servers mostly still use `max_tokens`. The first
    request uses the right form for the endpoint, and a 400 that names either parameter is retried
    once with that parameter fixed or dropped.
    """

    def __init__(self, s: LLMSettings):
        self.s = s
        self.url = (s.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        self.token_param = (
            "max_completion_tokens"
            if self.url.startswith("https://api.openai.com/")
            else "max_tokens"
        )
        self.send_temperature = True

    def complete(self, system, messages, *, json_schema=None) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        headers = {"Authorization": f"Bearer {self.s.api_key}"} if self.s.api_key else {}
        failures = 0
        for _ in range(8):
            body = {"model": self.s.model, "messages": msgs, self.token_param: self.s.max_tokens}
            if self.send_temperature:
                body["temperature"] = self.s.temperature
            try:
                r = httpx.post(self.url, headers=headers, timeout=self.s.timeout_s, json=body)
            except httpx.TransportError as e:  # DNS blip, reset, timeout: wait and try again
                failures += 1
                if failures > len(RETRY_DELAYS):
                    raise LLMError(f"network error after {failures} attempts: {e}") from e
                time.sleep(RETRY_DELAYS[failures - 1])
                continue
            if r.status_code in (429, 500, 502, 503, 504) and failures < len(RETRY_DELAYS):
                failures += 1
                time.sleep(RETRY_DELAYS[failures - 1])
                continue
            if r.status_code == 400:
                if "max_completion_tokens" in r.text and self.token_param == "max_tokens":
                    self.token_param = "max_completion_tokens"
                    continue
                if "'max_tokens'" in r.text and self.token_param == "max_completion_tokens":
                    self.token_param = "max_tokens"
                    continue
                if "temperature" in r.text and self.send_temperature:
                    self.send_temperature = False
                    continue
            break
        if r.is_error:
            raise LLMError(f"{r.status_code}: {r.text[:500]}")
        return r.json()["choices"][0]["message"]["content"]


class AnthropicBackend:
    def __init__(self, s: LLMSettings):
        if not s.api_key:
            raise ConfigError("TRF_LLM_API_KEY is required for the anthropic provider")
        self.s = s
        self.url = (s.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"

    def complete(self, system, messages, *, json_schema=None) -> str:
        body: dict = {
            "model": self.s.model,
            "messages": messages,
            "temperature": self.s.temperature,
            "max_tokens": self.s.max_tokens,
        }
        if system:
            body["system"] = system
        r = httpx.post(
            self.url,
            timeout=self.s.timeout_s,
            headers={"x-api-key": self.s.api_key or "", "anthropic-version": "2023-06-01"},
            json=body,
        )
        if r.is_error:
            raise LLMError(f"{r.status_code}: {r.text[:500]}")
        return "".join(p.get("text", "") for p in r.json()["content"] if p.get("type") == "text")


class OllamaBackend:
    """Native Ollama /api/chat. `json_schema` constrains decoding, which makes small local models
    far more reliable at structured output. Reasoning models keep their chain of thought in a
    separate field; `max_tokens` budgets reasoning + answer together."""

    def __init__(self, s: LLMSettings):
        self.s = s
        self.url = (s.base_url or "http://localhost:11434").rstrip("/") + "/api/chat"

    def complete(self, system, messages, *, json_schema=None) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        options: dict = {"temperature": self.s.temperature, "num_predict": self.s.max_tokens}
        if self.s.num_ctx:
            options["num_ctx"] = self.s.num_ctx
        body: dict = {"model": self.s.model, "messages": msgs, "stream": False, "options": options}
        if json_schema:
            body["format"] = json_schema
        r = httpx.post(self.url, timeout=self.s.timeout_s, json=body)
        if r.is_error:
            raise LLMError(f"{r.status_code}: {r.text[:500]}")
        content = r.json()["message"].get("content") or ""
        if not content.strip():
            raise LLMError(
                "empty reply: the token budget was probably spent on reasoning "
                "(raise TRF_LLM_MAX_TOKENS)"
            )
        return content


class CachingBackend:
    """Wraps a backend with an on-disk cache keyed by (model, system, messages, schema)."""

    def __init__(self, inner: Backend, cache_dir: str | Path, salt: str = ""):
        self.inner, self.dir, self.salt = inner, Path(cache_dir), salt
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = self.misses = 0

    def complete(self, system, messages, *, json_schema=None) -> str:
        key = hashlib.sha256(
            json.dumps([self.salt, system, messages, json_schema], sort_keys=True).encode()
        ).hexdigest()
        f = self.dir / f"{key}.json"
        if f.exists():
            self.hits += 1
            return json.loads(f.read_text(encoding="utf-8"))["reply"]
        reply = self.inner.complete(system, messages, json_schema=json_schema)
        f.write_text(json.dumps({"reply": reply}), encoding="utf-8")
        self.misses += 1
        return reply


BACKENDS: dict[str, type] = {
    "openai": OpenAIBackend,
    "anthropic": AnthropicBackend,
    "ollama": OllamaBackend,
}


def _extract_json(text: str) -> str:
    """Pull a JSON object out of a reply that may be fenced or wrapped in prose."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in reply")
    return text[start : end + 1]


class LLM:
    def __init__(self, backend: Backend):
        self.backend = backend
        self.calls = 0  # backend invocations that reached this wrapper (cached or not)

    @classmethod
    def from_env(cls) -> LLM:
        s = LLMSettings.from_env()
        try:
            backend: Backend = BACKENDS[s.provider](s)
        except KeyError:
            raise ConfigError(
                f"Unknown TRF_LLM_PROVIDER {s.provider!r}; supported: {sorted(BACKENDS)}"
            ) from None
        if s.cache_dir:
            backend = CachingBackend(backend, s.cache_dir, salt=f"{s.provider}:{s.model}")
        return cls(backend)

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.calls += 1
        return self.backend.complete(system, [{"role": "user", "content": prompt}])

    def structured(
        self, prompt: str, schema: type[T], *, system: str | None = None, max_retries: int = 2
    ) -> T:
        """Get a reply validated against `schema`; on failure, feed the error back and retry."""
        json_schema = schema.model_json_schema()
        sys_prompt = (
            (system + "\n\n" if system else "")
            + "Reply with ONLY a JSON object matching this JSON Schema, no prose:\n"
            + json.dumps(json_schema)
        )
        messages: list[Message] = [{"role": "user", "content": prompt}]
        last_err: Exception | None = None
        for _ in range(max_retries + 1):
            self.calls += 1
            reply = self.backend.complete(sys_prompt, messages, json_schema=json_schema)
            try:
                return schema.model_validate_json(_extract_json(reply))
            except (ValueError, ValidationError) as e:
                last_err = e
                messages += [
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": f"That reply was invalid: {e}\nReturn corrected JSON only.",
                    },
                ]
        raise LLMError(f"structured output failed after {max_retries + 1} attempts: {last_err}")

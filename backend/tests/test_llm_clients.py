"""Model adapters, tested with stubbed transports (no network)."""

from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm import openai_compatible
from app.llm.anthropic_client import AnthropicClient
from app.llm.client import LLMError, get_llm_client
from app.llm.openai_compatible import OpenAICompatibleClient


# --- factory -------------------------------------------------------------------------

def test_factory_requires_configuration():
    with pytest.raises(LLMError, match="No model configured"):
        get_llm_client(Settings(_env_file=None, llm_model="claude-opus-5-5"))


def test_empty_key_counts_as_not_configured():
    s = Settings(_env_file=None, llm_model="claude-opus-5-5", anthropic_api_key=SecretStr("  "))
    assert s.llm_configured is False


def test_factory_builds_anthropic_client():
    client = get_llm_client(Settings(_env_file=None, llm_model="claude-opus-5-5", anthropic_api_key=SecretStr("k")))
    assert isinstance(client, AnthropicClient) and client.name == "anthropic/claude-opus-5-5"


def test_factory_builds_openai_compatible_client():
    s = Settings(_env_file=None, llm_provider="openai_compatible", llm_model="llama3.1", llm_base_url="http://localhost:11434/v1")
    client = get_llm_client(s)
    assert isinstance(client, OpenAICompatibleClient) and client.name == "openai_compatible/llama3.1"


# --- Anthropic adapter ------------------------------------------------------------------

class StubMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def anthropic_with(model="claude-opus-5-5", **stub) -> tuple[AnthropicClient, StubMessages]:
    client = AnthropicClient(model=model, api_key="test-key", timeout=5)
    messages = StubMessages(**stub)
    client._client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return client, messages


def message(text="{}", stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


def test_anthropic_returns_text_blocks_only():
    client, _ = anthropic_with(response=message('{"fields": {}}'))
    assert client.complete("sys", "user") == '{"fields": {}}'


def test_anthropic_request_shape():
    client, stub = anthropic_with(response=message())
    client.complete("sys", "user")
    assert stub.kwargs["model"] == "claude-opus-5-5"
    assert stub.kwargs["system"] == "sys"
    assert stub.kwargs["messages"] == [{"role": "user", "content": "user"}]
    assert stub.kwargs["output_config"] == {"effort": "medium"}
    assert stub.kwargs["fallbacks"] == "default"
    assert stub.kwargs["betas"] == ["server-side-fallback-2026-07-01"]


def test_anthropic_no_fallbacks_for_models_without_support():
    client, stub = anthropic_with(model="claude-haiku-5-5", response=message())
    client.complete("sys", "user")
    assert "fallbacks" not in stub.kwargs and "betas" not in stub.kwargs


@pytest.mark.parametrize("stop_reason,match", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_anthropic_bad_stop_reasons_raise(stop_reason, match):
    client, _ = anthropic_with(response=message(stop_reason=stop_reason))
    with pytest.raises(LLMError, match=match):
        client.complete("sys", "user")


def _status_error(cls, code):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(code, request=request), body=None)


@pytest.mark.parametrize(
    "error,match",
    [
        (_status_error(anthropic.AuthenticationError, 401), "key was rejected"),
        (_status_error(anthropic.NotFoundError, 404), "Model not found"),
        (_status_error(anthropic.RateLimitError, 429), "Rate limited"),
        (_status_error(anthropic.InternalServerError, 500), r"error \(500\)"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), "network"),
    ],
)
def test_anthropic_errors_become_readable_llm_errors(error, match):
    client, _ = anthropic_with(error=error)
    with pytest.raises(LLMError, match=match) as exc_info:
        client.complete("sys", "user")
    assert "test-key" not in str(exc_info.value)


# --- OpenAI-compatible adapter --------------------------------------------------------------

def fake_post(status=200, body=None, raises=None, seen=None):
    def post(url, json, headers, timeout):
        if seen is not None:
            seen.update(url=url, json=json, headers=headers)
        if raises:
            raise raises
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))
    return post


def ok_body(text, finish="stop"):
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}]}


def test_openai_compatible_request_and_reply(monkeypatch):
    seen = {}
    monkeypatch.setattr(openai_compatible.httpx, "post", fake_post(body=ok_body('{"fields": {}}'), seen=seen))
    client = OpenAICompatibleClient("llama3.1", "http://localhost:11434/v1/", api_key=None, timeout=5)
    assert client.complete("sys", "user") == '{"fields": {}}'
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["json"]["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]
    assert seen["headers"] == {}  # no key -> no auth header


def test_openai_compatible_sends_key_when_given(monkeypatch):
    seen = {}
    monkeypatch.setattr(openai_compatible.httpx, "post", fake_post(body=ok_body("x"), seen=seen))
    OpenAICompatibleClient("gpt", "https://api.example.com/v1", api_key="k", timeout=5).complete("s", "u")
    assert seen["headers"] == {"Authorization": "Bearer k"}


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (dict(status=401, body={}), "rejected the API key"),
        (dict(status=404, body={}), "not found"),
        (dict(status=500, body={}), r"error \(500\)"),
        (dict(body={"unexpected": True}), "unexpected response"),
        (dict(body=ok_body("partial", finish="length")), "cut off"),
        (dict(body=ok_body("")), "empty"),
        (dict(raises=httpx.ConnectError("refused")), "Could not reach"),
        (dict(raises=httpx.ReadTimeout("slow")), "timed out"),
    ],
)
def test_openai_compatible_failures(monkeypatch, kwargs, match):
    monkeypatch.setattr(openai_compatible.httpx, "post", fake_post(**kwargs))
    with pytest.raises(LLMError, match=match):
        OpenAICompatibleClient("m", "http://localhost:1/v1", None, 5).complete("s", "u")

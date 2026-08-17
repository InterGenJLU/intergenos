# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Unit tests for the vendor-neutral cloud adapter substrate (intergen.cloud).

These exercise the PLATFORM-INDEPENDENT half — request shaping (endpoint, auth
headers, body) and response parsing — by stubbing the keyring lookup and faking
urllib's transport to capture the outbound Request and return a canned provider
response. No network, no libsecret; runs anywhere. The live libsecret fetch and
the real provider HTTP are exercised separately on Linux with real keys.
"""
import json

import pytest

from intergen.cloud import CloudAdapterError, create_adapter
from intergen.cloud import http_adapter as ha
from intergen.interfaces.cloud import ProviderConfig
from intergen.interfaces.types import Message, MessageRole


class _FakeResp:
    def __init__(self, payload: dict):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _stub_keyring(monkeypatch):
    """Every adapter gets a deterministic fake key, never a real keyring."""
    monkeypatch.setattr(ha, "lookup_secret", lambda kid: f"TEST-KEY-{kid}")


def _capture(monkeypatch, payload: dict) -> dict:
    cap: dict = {}

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["data"] = json.loads(req.data.decode())
        cap["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _FakeResp(payload)

    monkeypatch.setattr(ha.urllib.request, "urlopen", fake_urlopen)
    return cap


def _cfg(adapter: str, model: str = "m1", base_url: str | None = None) -> ProviderConfig:
    return ProviderConfig(
        name=adapter,
        adapter=adapter,
        model=model,
        api_key_keyring_id=f"kid-{adapter}",
        base_url=base_url,
    )


def _msgs():
    return [
        Message(role=MessageRole.SYSTEM, content="be brief"),
        Message(role=MessageRole.USER, content="hello"),
    ]


# -- OpenAI-compatible family -------------------------------------------------

def test_openai_shaping_and_parse(monkeypatch):
    cap = _capture(monkeypatch, {
        "choices": [{"message": {"content": "hi there"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        "model": "m1",
    })
    adapter = create_adapter(_cfg("openai"))
    resp = adapter.send(_msgs(), max_tokens=10, temperature=0.2)

    assert cap["url"] == "https://api.openai.com/v1/chat/completions"
    assert cap["headers"]["authorization"] == "Bearer TEST-KEY-kid-openai"
    assert cap["data"]["model"] == "m1"
    assert cap["data"]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]
    assert cap["data"]["max_tokens"] == 10
    assert cap["data"]["temperature"] == 0.2
    assert resp.text == "hi there"
    assert resp.local is False
    assert resp.tokens_prompt == 3 and resp.tokens_completion == 2


@pytest.mark.parametrize("adapter,url", [
    ("xai", "https://api.x.ai/v1/chat/completions"),
    ("deepseek", "https://api.deepseek.com/v1/chat/completions"),
])
def test_openai_compatible_endpoints(monkeypatch, adapter, url):
    cap = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}}], "usage": {}})
    create_adapter(_cfg(adapter)).send(_msgs())
    assert cap["url"] == url
    assert cap["headers"]["authorization"].startswith("Bearer ")


def test_custom_requires_base_url(monkeypatch):
    _capture(monkeypatch, {})
    with pytest.raises(CloudAdapterError):
        create_adapter(_cfg("custom")).send(_msgs())


def test_custom_uses_supplied_base_url(monkeypatch):
    cap = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}}], "usage": {}})
    create_adapter(_cfg("custom", base_url="https://local.example/v1")).send(_msgs())
    assert cap["url"] == "https://local.example/v1/chat/completions"


# -- Microsoft Azure OpenAI ---------------------------------------------------

def test_microsoft_azure_endpoint_and_auth(monkeypatch):
    cap = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}}], "usage": {}})
    cfg = _cfg("microsoft", model="mydeploy", base_url="https://res.openai.azure.com")
    create_adapter(cfg).send(_msgs())
    assert cap["url"] == (
        "https://res.openai.azure.com/openai/deployments/mydeploy"
        "/chat/completions?api-version=2024-06-01"
    )
    assert cap["headers"]["api-key"] == "TEST-KEY-kid-microsoft"
    assert "authorization" not in cap["headers"]


def test_microsoft_requires_base_url(monkeypatch):
    _capture(monkeypatch, {})
    with pytest.raises(CloudAdapterError):
        create_adapter(_cfg("microsoft", model="d")).send(_msgs())


# -- Anthropic native ---------------------------------------------------------

def test_anthropic_native_shaping_and_parse(monkeypatch):
    cap = _capture(monkeypatch, {
        "content": [{"type": "text", "text": "hello"}],
        "usage": {"input_tokens": 5, "output_tokens": 2},
        "model": "claude-x",
    })
    resp = create_adapter(_cfg("anthropic", model="claude-x")).send(_msgs(), max_tokens=50)

    assert cap["url"] == "https://api.anthropic.com/v1/messages"
    assert cap["headers"]["x-api-key"] == "TEST-KEY-kid-anthropic"
    assert cap["headers"]["anthropic-version"] == "2023-06-01"
    # system is top-level, NOT a message
    assert cap["data"]["system"] == "be brief"
    assert cap["data"]["messages"] == [{"role": "user", "content": "hello"}]
    assert cap["data"]["max_tokens"] == 50
    assert resp.text == "hello"
    assert resp.tokens_prompt == 5 and resp.tokens_completion == 2


# -- Google Gemini native -----------------------------------------------------

def test_google_native_shaping_and_parse(monkeypatch):
    cap = _capture(monkeypatch, {
        "candidates": [{"content": {"parts": [{"text": "hey"}]}}],
        "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 1},
    })
    resp = create_adapter(_cfg("google", model="gemini-x")).send(_msgs())

    assert cap["url"] == (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-x:generateContent"
    )
    assert cap["headers"]["x-goog-api-key"] == "TEST-KEY-kid-google"
    assert cap["data"]["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert cap["data"]["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert resp.text == "hey"
    assert resp.tokens_prompt == 4 and resp.tokens_completion == 1


# -- Factory ------------------------------------------------------------------

def test_factory_unknown_adapter_is_hard_error():
    # 'mistral' is intentionally NOT in the ratified set (default-deny).
    with pytest.raises(CloudAdapterError):
        create_adapter(_cfg("mistral"))


def test_factory_builds_all_seven():
    expected = {"anthropic", "google", "microsoft", "openai", "xai", "deepseek", "custom"}
    for name in expected:
        base = "https://x.example" if name in ("custom", "microsoft") else None
        assert create_adapter(_cfg(name, base_url=base)).name == name


# -- test_connection ----------------------------------------------------------

def test_connection_ok(monkeypatch):
    _capture(monkeypatch, {"choices": [{"message": {"content": "pong"}}], "usage": {}})
    ok, _ = create_adapter(_cfg("openai")).test_connection()
    assert ok is True


def test_connection_reports_key_failure(monkeypatch):
    def _raise(_kid):
        raise CloudAdapterError("no key in keyring")
    monkeypatch.setattr(ha, "lookup_secret", _raise)
    ok, msg = create_adapter(_cfg("openai")).test_connection()
    assert ok is False
    assert "key lookup failed" in msg


def test_http_error_becomes_cloud_adapter_error(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(ha.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(CloudAdapterError):
        create_adapter(_cfg("openai")).send(_msgs())


# -- TLS guard (HG#8: never send the API key over cleartext) ------------------

def test_refuses_api_key_over_plain_http(monkeypatch):
    # A user-supplied http base_url to a real host must be refused before the
    # key is attached — otherwise the auth header crosses the wire in cleartext.
    called = {"key": False}

    def _track(_kid):
        called["key"] = True
        return "TEST-KEY"
    monkeypatch.setattr(ha, "lookup_secret", _track)
    _capture(monkeypatch, {"choices": [{"message": {"content": "x"}}], "usage": {}})
    adapter = create_adapter(_cfg("custom", base_url="http://api.example.com/v1"))
    with pytest.raises(CloudAdapterError):
        adapter.send(_msgs())
    # the guard fires BEFORE the keyring is touched
    assert called["key"] is False


def test_allows_http_loopback(monkeypatch):
    cap = _capture(monkeypatch, {"choices": [{"message": {"content": "x"}}], "usage": {}})
    adapter = create_adapter(_cfg("custom", base_url="http://127.0.0.1:1234/v1"))
    adapter.send(_msgs())  # local server has no wire — allowed
    assert cap["url"].startswith("http://127.0.0.1:1234")


def test_allows_https(monkeypatch):
    cap = _capture(monkeypatch, {"choices": [{"message": {"content": "x"}}], "usage": {}})
    create_adapter(_cfg("custom", base_url="https://api.example.com/v1")).send(_msgs())
    assert cap["url"].startswith("https://")

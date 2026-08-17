"""Env-var override allow-list — security-boundary contract tests.

`Config._load_env_overrides` narrows the env tier to a fixed allow-list of
safe-by-construction tunables (the hardening that replaced the original
blanket `INTERGEN_*` loop, which accepted dangerous path keys like
llm.endpoint / memory.db_path — a prompt-injection persistence vector).

These tests lock the contract that matters for the config-sentinel change:

  - INTERGEN_TRACE (metadata-only decision tracer switch) IS sanctioned:
    it sets trace.enabled and does NOT trip the refusal warning.
  - INTERGEN_TRACE_CONTENT (raw prompt/tool-arg/output capture) is
    DELIBERATELY NOT sanctioned — it must stay refused so the content tap
    is never blessed through the sanctioned env boundary (the conservative
    security-only posture).
  - A path-style key (llm.endpoint) stays refused (the original hardening).
  - _validate_bool maps the conservative truthy spellings, junk → False.
"""

from __future__ import annotations

import logging

import pytest

from intergen.config import Config


@pytest.fixture
def clean_env(monkeypatch):
    """Strip every INTERGEN_* var so each test controls the environment."""
    import os
    for k in list(os.environ):
        if k.startswith("INTERGEN_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_intergen_trace_is_sanctioned(clean_env, caplog):
    clean_env.setenv("INTERGEN_TRACE", "1")
    with caplog.at_level(logging.WARNING):
        cfg = Config()
    assert cfg.get("trace.enabled") is True
    # The whole point of the sentinel change: a sanctioned tunable must not
    # trip the refusal warning.
    assert not any(
        "Refusing INTERGEN_TRACE:" in r.getMessage() for r in caplog.records
    )


def test_trace_content_stays_refused(clean_env, caplog):
    """The content tap must NOT be sanctioned through the env boundary."""
    clean_env.setenv("INTERGEN_TRACE_CONTENT", "1")
    with caplog.at_level(logging.WARNING):
        cfg = Config()
    # Not promoted to a config key...
    assert cfg.get("trace.content") is None
    assert cfg.get("trace.enabled") is None
    # ...and explicitly refused.
    assert any(
        "Refusing INTERGEN_TRACE_CONTENT" in r.getMessage()
        for r in caplog.records
    )


def test_trace_disabled_spellings(clean_env):
    for val in ("0", "false", "no", "off", "", "garbage"):
        clean_env.setenv("INTERGEN_TRACE", val)
        cfg = Config()
        assert cfg.get("trace.enabled") is False, f"{val!r} should disable"


def test_trace_enabled_spellings(clean_env):
    for val in ("1", "true", "TRUE", "yes", "On"):
        clean_env.setenv("INTERGEN_TRACE", val)
        cfg = Config()
        assert cfg.get("trace.enabled") is True, f"{val!r} should enable"


def test_path_style_key_still_refused(clean_env, caplog):
    """A path-aware override is the original hardening target — stays refused."""
    clean_env.setenv("INTERGEN_LLM_ENDPOINT", "http://evil.example/v1")
    with caplog.at_level(logging.WARNING):
        cfg = Config()
    assert cfg.get("llm.endpoint") != "http://evil.example/v1"
    assert any(
        "Refusing INTERGEN_LLM_ENDPOINT" in r.getMessage()
        for r in caplog.records
    )


def test_validate_bool_unit():
    assert Config._validate_bool("on") is True
    assert Config._validate_bool("1") is True
    assert Config._validate_bool("true") is True
    assert Config._validate_bool("yes") is True
    assert Config._validate_bool("0") is False
    assert Config._validate_bool("off") is False
    assert Config._validate_bool("nonsense") is False

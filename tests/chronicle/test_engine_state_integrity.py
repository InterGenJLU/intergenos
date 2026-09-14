"""Unreadable state must not reset sequence, pins, or target metadata."""

import os

import pytest

from chronicle import config, engine, paths


@pytest.mark.parametrize("payload", [b"{malformed", b"\xff", b"[]"])
def test_invalid_state_is_refused_and_preserved(tmp_path, payload):
    eng = engine.Engine(local_root=tmp_path, config=config.Config())
    eng.pin("keep-this-version")
    state = paths.state_path(tmp_path)
    state.write_bytes(payload)

    with pytest.raises(engine.EngineError, match="state"):
        engine.Engine(local_root=tmp_path, config=config.Config())

    assert state.read_bytes() == payload


def test_read_failure_is_refused_without_replacing_state(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("an unprivileged account is required for this read failure")
    eng = engine.Engine(local_root=tmp_path, config=config.Config())
    eng.pin("keep-this-version")
    state = paths.state_path(tmp_path)
    original = state.read_bytes()
    state.chmod(0)
    try:
        with pytest.raises(engine.EngineError, match="state"):
            engine.Engine(local_root=tmp_path, config=config.Config())
    finally:
        state.chmod(0o600)

    assert state.read_bytes() == original


def test_absent_initial_state_keeps_fresh_store_defaults(tmp_path):
    eng = engine.Engine(local_root=tmp_path, config=config.Config())

    assert eng.state["sequence"] == 0
    assert eng.state["pins"] == []
    assert eng.state["target"] is None


def test_valid_older_state_keeps_pins_and_receives_missing_fields(tmp_path):
    eng = engine.Engine(local_root=tmp_path, config=config.Config())
    state = paths.state_path(tmp_path)
    state.write_text('{"sequence": 8, "pins": ["keep-this-version"]}')

    restored = engine.Engine(local_root=tmp_path, config=config.Config())

    assert restored.state["sequence"] == 8
    assert restored.state["pins"] == ["keep-this-version"]
    assert restored.state["retention_events"] == []

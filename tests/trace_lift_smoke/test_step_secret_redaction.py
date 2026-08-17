# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Regression tests for the step-decorator secret-redaction bypass (PI-ge9b04-D).

The ge9b-04 dogfood install wrote the wizard-entered root + user passwords
in plaintext into the world-readable target trace: `trace_install_step` emitted
`args_positional` raw, and only kwargs passed the REDACT_KEYS scrubber, so
`set_root_password(target, password)` called POSITIONALLY bypassed redaction
entirely. These tests pin the fix:

  1. A secret passed positionally is redacted by its declared parameter NAME.
  2. A secret passed named still redacts (no regression).
  3. Non-secret args survive un-redacted (forensic value preserved).
  4. Positions with no resolvable name (*args) fail CLOSED — redacted.
  5. Trace sinks are created 0600 and a pre-existing looser sink is
     tightened (the leaked-at-rest file was mode 644).
"""

import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IGOS_TRACE_PY = REPO_ROOT / "scripts" / "lib" / "igos_trace.py"


def _load_trace_module(tmpdir):
    """Load a FRESH igos_trace with verbose on and an isolated sink root.

    Module-level _VERBOSE is read at import, so the env must be set before
    exec; a unique sys.modules key keeps parallel test files independent.
    """
    os.environ["IGOS_BUILD_DEBUG_VERBOSE"] = "1"
    os.environ["IGOS_TRACE_ROOT"] = str(tmpdir)
    key = f"_igos_trace_redaction_test_{os.getpid()}_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(key, str(IGOS_TRACE_PY))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def _events_from(sink_path):
    with open(sink_path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _fresh_mod_and_sink():
    tmpdir = tempfile.mkdtemp(prefix="trace-redact-test-")
    mod = _load_trace_module(tmpdir)
    sink_path = Path(tmpdir) / "sink.jsonl"
    handle = mod._open_600(sink_path)
    mod._SINKS.append(handle)
    return mod, sink_path


def test_positional_secret_is_redacted_by_parameter_name():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("set_root_password")
    def set_root_password(target, password):
        return None

    set_root_password("/mnt/target", "hunter2-plaintext")

    raw = sink.read_text()
    assert "hunter2-plaintext" not in raw, (
        "positionally-passed password leaked into the trace — the "
        "PI-ge9b04-D bypass is back"
    )
    enter = [e for e in _events_from(sink) if e["type"] == "step_enter"][0]
    assert enter["args_positional"] == ["/mnt/target", "<REDACTED>"]


def test_named_secret_still_redacts_and_nonsecrets_survive():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("create_user")
    def create_user(target, username, password, groups=None):
        return None

    create_user("/mnt/target", "alice", password="s3cret", groups="wheel")

    raw = sink.read_text()
    assert "s3cret" not in raw
    enter = [e for e in _events_from(sink) if e["type"] == "step_enter"][0]
    # Forensic value preserved: non-secret values remain readable.
    assert enter["args_positional"] == ["/mnt/target", "alice"]
    assert enter["kwargs"]["password"] == "<REDACTED>"
    assert enter["kwargs"]["groups"] == "wheel"


def test_unresolvable_positions_fail_closed():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("varargs_step")
    def varargs_step(*args):
        return None

    varargs_step("maybe-a-secret", "also-unknown")

    enter = [e for e in _events_from(sink) if e["type"] == "step_enter"][0]
    assert enter["args_positional"] == ["<REDACTED>", "<REDACTED>"], (
        "a position with no resolvable parameter name must redact, not emit"
    )


def test_sink_created_0600_and_existing_sink_tightened():
    tmpdir = tempfile.mkdtemp(prefix="trace-perm-test-")
    mod = _load_trace_module(tmpdir)

    fresh = Path(tmpdir) / "fresh.jsonl"
    handle = mod._open_600(fresh)
    handle.close()
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600

    loose = Path(tmpdir) / "loose.jsonl"
    loose.touch()
    os.chmod(loose, 0o644)  # the leaked-at-rest mode on the dogfood install
    handle = mod._open_600(loose)
    handle.close()
    assert stat.S_IMODE(loose.stat().st_mode) == 0o600

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A traced step never records a return made from a secret it was handed.

WHAT WAS WRONG (an R001.3 install, 2026-10-16). `trace_install_step` redacted a
step's secret ARGUMENTS but wrote its RETURN value verbatim. users.py
set_root_password returns the crypt hash of the password it was given (the
installer keeps it for verify_root_password_kept), so the target's
/var/log/forge-install-*.log carried root's password hash in the step_exit row.

WHAT THIS PROVES. (1) A step with a parameter named in REDACT_KEYS has its
return withheld whatever the return's shape, passed by position or by name.
(2) A credential-shaped return is withheld from any step. (3) An ordinary
return from a step handed no secret stays readable. (4) An exception's text
passes the same shape check. (5) A trace an earlier writer left on disk is
brought to the same rule by redact_recorded_step_returns: only the offending
rows change, the file stays 0600 with its owner, a row records the change,
and a second run changes nothing. (6) The module's command line does the same
and fails loudly on a file it cannot process.
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IGOS_TRACE_PY = REPO_ROOT / "scripts" / "lib" / "igos_trace.py"

CRYPT_HASH = "$y$j9T$saltsaltsaltsaltsalt$" + "H" * 43


def _load_trace_module(tmpdir):
    os.environ["IGOS_BUILD_DEBUG_VERBOSE"] = "1"
    os.environ["IGOS_TRACE_ROOT"] = str(tmpdir)
    key = f"_igos_trace_return_test_{os.getpid()}_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(key, str(IGOS_TRACE_PY))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def _fresh_mod_and_sink():
    tmpdir = tempfile.mkdtemp(prefix="trace-return-test-")
    mod = _load_trace_module(tmpdir)
    sink_path = Path(tmpdir) / "sink.jsonl"
    mod._SINKS.append(mod._open_600(sink_path))
    return mod, sink_path


def _events(sink_path, kind):
    with open(sink_path, encoding="utf-8") as fh:
        return [e for e in (json.loads(l) for l in fh if l.strip())
                if e["type"] == kind]


def test_secret_bearing_step_return_is_withheld_positional_and_named():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("set_root_password")
    def set_root_password(target, password):
        return CRYPT_HASH

    assert set_root_password("/mnt/target", "hunter2-plaintext") == CRYPT_HASH
    assert set_root_password("/mnt/target", password="hunter2-plaintext") == CRYPT_HASH

    raw = sink.read_text()
    assert CRYPT_HASH not in raw and "j9T$salt" not in raw, (
        "the step's return — the crypt hash of the password it was handed — "
        "reached the trace")
    exits = _events(sink, "step_exit")
    assert [e["return"] for e in exits] == ["<REDACTED>", "<REDACTED>"]
    assert [e["return_redacted"] for e in exits] == [
        "secret-bearing step: takes password"] * 2


def test_secret_bearing_step_plain_return_is_withheld_too():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("derive")
    def derive(target, passphrase):
        return "a-value-made-from-the-passphrase"

    derive("/mnt/target", "s3cret")
    assert "a-value-made-from-the-passphrase" not in sink.read_text()
    (exit_row,) = _events(sink, "step_exit")
    assert exit_row["return"] == "<REDACTED>"
    assert exit_row["return_redacted"] == "secret-bearing step: takes passphrase"


def test_credential_shaped_return_is_withheld_from_any_step():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("read_field")
    def read_field(target):
        return "root:" + CRYPT_HASH

    read_field("/mnt/target")
    assert CRYPT_HASH not in sink.read_text()
    (exit_row,) = _events(sink, "step_exit")
    assert exit_row["return"] == "<REDACTED>"
    assert exit_row["return_redacted"] == "credential-shaped return"


def test_ordinary_return_of_a_step_handed_no_secret_stays_readable():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("seed_user_monitor_layout")
    def seed(target, username):
        return True

    @mod.trace_install_step("enable_services")
    def enable(target):
        return None

    seed("/mnt/target", "alice")
    enable("/mnt/target")
    exits = _events(sink, "step_exit")
    assert [(e["return"], e["return_redacted"]) for e in exits] == [
        ("True", None), (None, None)]


def test_varargs_step_return_fails_closed():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("varargs_step")
    def varargs_step(*args):
        return "maybe-made-from-a-secret"

    varargs_step("maybe-a-secret")
    (exit_row,) = _events(sink, "step_exit")
    assert exit_row["return"] == "<REDACTED>"
    assert exit_row["return_redacted"].startswith("secret-bearing step")


def test_exception_text_passes_the_shape_check():
    mod, sink = _fresh_mod_and_sink()

    @mod.trace_install_step("leaky")
    def leaky(target):
        raise ValueError("field was " + CRYPT_HASH)

    @mod.trace_install_step("plain")
    def plain(target):
        raise RuntimeError("chpasswd exited 1")

    for fn in (leaky, plain):
        try:
            fn("/mnt/target")
        except (ValueError, RuntimeError):
            pass
    assert CRYPT_HASH not in sink.read_text()
    rows = _events(sink, "step_exception")
    assert rows[0]["exception"] == "<REDACTED>"
    assert rows[0]["exception_redacted"] == "credential-shaped exception"
    assert rows[1]["exception"] == "RuntimeError('chpasswd exited 1')"
    assert rows[1]["exception_redacted"] is None


def _earlier_writer_trace(directory):
    """A trace in the shape the R001.3 -02 writer left on the target."""
    rows = [
        {"type": "trace_init", "runid": "0a560944392341b1"},
        {"type": "step_enter", "step": "set_root_password",
         "args_positional": ["/mnt/target"], "kwargs": {"password": "<REDACTED>"}},
        {"type": "subprocess_start", "cmd": ["chpasswd", "--root", "/mnt/target", "-e"],
         "stdin": "<REDACTED>", "stdin_redacted": "credential consumer: chpasswd"},
        {"type": "password_hash_format", "format": "$y$", "fallback": False},
        {"type": "step_exit", "step": "set_root_password", "return": CRYPT_HASH,
         "duration_ms": 62},
        {"type": "step_enter", "step": "create_user",
         "args_positional": ["/mnt/target", "alice"], "kwargs": {"password": "<REDACTED>"}},
        {"type": "step_exit", "step": "create_user", "return": None, "duration_ms": 9},
        {"type": "step_enter", "step": "seed_user_monitor_layout",
         "args_positional": ["/mnt/target", "alice"], "kwargs": {}},
        {"type": "step_exit", "step": "seed_user_monitor_layout", "return": "True",
         "duration_ms": 3},
        {"type": "target_sink_detached", "path": "/mnt/target/var/log/x.log"},
    ]
    path = Path(directory) / "forge-install-20261016T235254Z-0a560944392341b1.log"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.chmod(path, 0o600)
    return path, rows


def test_trace_on_disk_is_redacted_once_and_only_where_needed():
    mod, _ = _fresh_mod_and_sink()
    tmp = tempfile.mkdtemp(prefix="trace-at-rest-")
    path, rows = _earlier_writer_trace(tmp)
    before = path.read_bytes().splitlines(keepends=True)
    st_before = path.stat()

    assert mod.redact_recorded_step_returns(path, by="test") == 1

    after = path.read_bytes().splitlines(keepends=True)
    assert CRYPT_HASH.encode() not in path.read_bytes()
    assert len(after) == len(before) + 1
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert changed == [4], f"only the step_exit row may change; changed {changed}"
    row = json.loads(after[4])
    assert row["return"] == "<REDACTED>"
    assert row["return_redacted"] == (
        "secret-bearing step: it was handed a secret (withheld at rest)")
    assert json.loads(after[3])["format"] == "$y$", "the format record stays"
    note = json.loads(after[-1])
    assert note["type"] == "trace_redacted_at_rest"
    assert note["rows_withheld"] == 1 and note["by"] == "test"
    st_after = path.stat()
    assert stat.S_IMODE(st_after.st_mode) == 0o600
    assert (st_after.st_uid, st_after.st_gid) == (st_before.st_uid, st_before.st_gid)
    leftovers = [p.name for p in Path(tmp).iterdir() if p.name != path.name]
    assert leftovers == [], f"temporary files left behind: {leftovers}"

    second = path.read_bytes()
    assert mod.redact_recorded_step_returns(path, by="test") == 0
    assert path.read_bytes() == second, "a second run must not touch the file"


def test_command_line_redacts_and_fails_loudly_on_an_unreadable_file():
    tmp = tempfile.mkdtemp(prefix="trace-at-rest-cli-")
    path, _ = _earlier_writer_trace(tmp)
    missing = Path(tmp) / "forge-install-missing.log"
    r = subprocess.run(
        [sys.executable, "-I", "-B", str(IGOS_TRACE_PY), "redact-step-returns",
         "--by", "forge 1.0.0 post-install", str(path), str(missing)],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, r
    assert f"{path}: withheld 1 recorded return(s)" in r.stdout
    assert f"{missing}: NOT REDACTED" in r.stderr
    assert CRYPT_HASH.encode() not in path.read_bytes()

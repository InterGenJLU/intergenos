# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The trace writer never records a credential fed to a subprocess on stdin.

WHAT WAS WRONG (R001.3 row 18; the AMD desktop PC's post-install evaluation and
the Zephyrus register, 2026-09-04). The step layer redacted the `password`
argument of set_root_password / create_user, but the subprocess layer under it
logged the full `chpasswd -e` stdin — `root:$6$...` and `user:$6$...` — into
the install trace. The file is root-only, but the framework directs seats to
read that trace end to end, and a hash in a log is a hash in a log.

WHAT THIS PROVES. (1) For a credential consumer (chpasswd, cryptsetup, ...) the
subprocess_start event carries the byte count and a `stdin_redacted` reason,
never the bytes — against the REAL chpasswd binary. (2) A chroot-wrapped
consumer is recognised through the wrapper. (3) An ordinary payload (an nft
ruleset fed to `cat`) is still recorded verbatim, so forensic value is kept.
(4) A credential-shaped payload fed to an unlisted consumer is redacted too,
and so is credential-shaped OUTPUT (openssl passwd prints the hash it made; a
tool echoing its input), with byte counts kept. (5) Byte counts are exact.
"""

import importlib.util
import os
import shutil
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IGOS_TRACE_PY = REPO_ROOT / "scripts" / "lib" / "igos_trace.py"

HASH_LINE = "root:$6$saltsaltsalt$" + "A" * 86 + "\nuser:$6$saltsaltsal2$" + "B" * 86 + "\n"
RULESET = "table inet filter {\n    chain input {\n        tcp dport 22 accept\n    }\n}\n"


def _fresh(tmpdir):
    """A fresh trace module with verbose on and one 0600 sink under tmpdir
    (the same wiring tests/trace_lift_smoke/test_step_secret_redaction.py uses)."""
    import sys
    os.environ["IGOS_BUILD_DEBUG_VERBOSE"] = "1"
    os.environ["IGOS_TRACE_ROOT"] = str(tmpdir)
    key = f"_igos_trace_stdin_test_{os.getpid()}_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(key, str(IGOS_TRACE_PY))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    sink = Path(tmpdir) / "sink.jsonl"
    mod._SINKS.append(mod._open_600(sink))
    mod._test_sink = sink
    return mod


def _events(mod):
    import json
    events = []
    with open(mod._test_sink, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _starts(mod):
    return [e for e in _events(mod) if e.get("type") == "subprocess_start"], mod._test_sink


class _Case:
    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mod = _fresh(self.tmp)
        return self

    def __exit__(self, *a):
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_real_chpasswd_stdin_is_withheld_with_its_byte_count():
    with _Case() as c:
        # `chpasswd --help` exits without reading stdin and needs no privilege;
        # the classifier keys on the command, so this is the real consumer.
        c.mod.traced_run(["chpasswd", "--help"], input=HASH_LINE, phase="users",
                         intent="set root password (test)")
        starts, sink = _starts(c.mod)
        ev = [e for e in starts if e["cmd"][0] == "chpasswd"][0]
        assert ev["stdin"] == "<REDACTED>"
        assert ev["stdin_redacted"] == "credential consumer: chpasswd"
        assert ev["stdin_bytes"] == len(HASH_LINE.encode())
        raw = sink.read_text()
        assert "$6$" not in raw and "AAAA" not in raw and "BBBB" not in raw


def test_chroot_wrapped_consumer_is_recognised():
    with _Case() as c:
        c.mod.traced_run(["chroot", "/nonexistent-target", "chpasswd", "-e"], input=HASH_LINE)
        starts, sink = _starts(c.mod)
        ev = starts[-1]
        assert ev["stdin"] == "<REDACTED>"
        assert "chpasswd" in ev["stdin_redacted"]
        assert "$6$" not in sink.read_text()


def test_cryptsetup_passphrase_is_withheld():
    with _Case() as c:
        c.mod.traced_run(["cryptsetup", "--version"], input="correct horse battery staple")
        starts, sink = _starts(c.mod)
        assert starts[-1]["stdin_redacted"] == "credential consumer: cryptsetup"
        assert "correct horse" not in sink.read_text()


def test_ordinary_payload_stays_verbatim():
    with _Case() as c:
        c.mod.traced_run(["cat"], input=RULESET, intent="feed a ruleset")
        starts, _ = _starts(c.mod)
        ev = starts[-1]
        assert ev["stdin"] == RULESET
        assert ev["stdin_redacted"] is None
        assert ev["stdin_bytes"] == len(RULESET.encode())


def test_credential_shaped_payload_to_an_unlisted_consumer_is_withheld():
    with _Case() as c:
        # `cat` echoes its input: the stdout side must be withheld too, or the
        # hash leaks through the subprocess_end event instead.
        c.mod.traced_run(["cat"], input=HASH_LINE)
        starts, sink = _starts(c.mod)
        assert starts[-1]["stdin_redacted"] == "credential-shaped payload"
        ends = [e for e in _events(c.mod) if e.get("type") == "subprocess_end"]
        assert ends[-1]["stdout"] == "<REDACTED>"
        assert ends[-1]["stdout_redacted"] == "credential-shaped output"
        assert ends[-1]["stdout_bytes"] == len(HASH_LINE.encode())
        assert "$6$" not in sink.read_text()
        c.mod.traced_run(["cat"], input="user:plaintextpassword\n")
        starts, sink = _starts(c.mod)
        assert starts[-1]["stdin_redacted"] == "credential-shaped payload"
        assert "plaintextpassword" not in sink.read_text()


def test_yaml_and_prose_are_not_mistaken_for_credentials():
    with _Case() as c:
        for payload in ("name: forge\nrelease: 248\n", "hello: this is prose, not a secret\n"):
            c.mod.traced_run(["cat"], input=payload)
            starts, _ = _starts(c.mod)
            assert starts[-1]["stdin_redacted"] is None, payload
            assert starts[-1]["stdin"] == payload

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Exercise the shipped trace descriptor functions in disposable Bash children."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/lib/trace.sh"


def trace_functions():
    source = SOURCE.read_text()
    functions = []
    for name in ("trace_init", "trace_close"):
        match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", source, re.M | re.S)
        if match is None:
            raise AssertionError(f"cannot extract {name} from {SOURCE}")
        functions.append(match.group())
    return "\n".join(functions)


SETUP = r"""
set -euo pipefail
_TRACE_VERBOSE=1
_TRACE_FD=-1
_TRACE_SINK_PATH=""
_TRACE_ROOT="$1"
_TRACE_RUNID=fixture
_TRACE_START_TS=timestamp
_trace_emit_event() { printf 'event:%s\n' "$1" >&"$_TRACE_FD"; }
"""


class TestTraceStderr(unittest.TestCase):
    def run_shell(self, body):
        with tempfile.TemporaryDirectory(prefix="trace-descriptor-") as tmp:
            root = Path(tmp)
            script = root / "fixture.sh"
            script.write_text(SETUP + trace_functions() + "\n" + body)
            env = {key: value for key, value in os.environ.items()
                   if key not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")}
            proc = subprocess.run(
                ["bash", "--noprofile", "--norc", str(script), str(root)],
                cwd=root, env=env, capture_output=True, text=True, timeout=10,
            )
            sinks = {p.name: p.read_text() for p in root.glob("*.jsonl")
                     if p.is_file()}
            return proc, sinks

    def test_init_preserves_stderr_and_opens_writable_sink(self):
        proc, sinks = self.run_shell(r"""
printf 'before\n' >&2
trace_init alpha
printf 'payload\n' >&"$_TRACE_FD"
printf 'after-init\n' >&2
trace_close
printf 'after-close\n' >&2
""")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "before\nafter-init\nafter-close\n")
        self.assertEqual(sinks, {"build-alpha-timestamp-fixture.jsonl":
                                 "event:trace_init\npayload\n"})

    def test_close_preserves_stderr_independently(self):
        proc, _ = self.run_shell(r"""
exec {_TRACE_FD}>>"$1/manual.jsonl"
old_fd=$_TRACE_FD
printf 'before\n' >&2
trace_close
printf 'after-close\n' >&2
[[ $_TRACE_FD == -1 && -z $_TRACE_SINK_PATH ]]
[[ ! -e /proc/self/fd/$old_fd ]]
trace_close
printf 'after-second-close\n' >&2
""")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "before\nafter-close\nafter-second-close\n")

    def test_reinitialization_preserves_stderr_and_switches_sink(self):
        proc, sinks = self.run_shell(r"""
trace_init alpha
printf 'first\n' >&"$_TRACE_FD"
trace_init beta
printf 'second\n' >&"$_TRACE_FD"
printf 'after-reinit\n' >&2
trace_close
""")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "after-reinit\n")
        self.assertEqual(sinks, {
            "build-alpha-timestamp-fixture.jsonl": "event:trace_init\nfirst\n",
            "build-beta-timestamp-fixture.jsonl": "event:trace_init\nsecond\n",
        })

    def test_failed_open_warns_and_disables_tracing(self):
        proc, sinks = self.run_shell(r"""
mkdir "$1/build-alpha-timestamp-fixture.jsonl"
trace_init alpha
printf 'after-failed-open\n' >&2
[[ $_TRACE_VERBOSE == 0 && $_TRACE_FD == -1 ]]
""")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("trace.sh: could not open sink ", proc.stderr)
        self.assertIn("trace disabled for this run\n", proc.stderr)
        self.assertTrue(proc.stderr.endswith("after-failed-open\n"))
        self.assertEqual(sinks, {})

    def test_disabled_tracing_is_a_noop(self):
        proc, sinks = self.run_shell(r"""
_TRACE_VERBOSE=0
trace_init alpha
trace_close
printf 'disabled\n' >&2
[[ $_TRACE_FD == -1 ]]
""")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "disabled\n")
        self.assertEqual(sinks, {})

    def test_caller_failure_keeps_status_and_diagnostic(self):
        proc, _ = self.run_shell(r"""
trace_init alpha
printf 'synthetic step failed\n' >&2
exit 23
""")
        self.assertEqual(proc.returncode, 23)
        self.assertEqual(proc.stderr, "synthetic step failed\n")


if __name__ == "__main__":
    unittest.main()

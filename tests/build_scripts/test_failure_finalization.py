# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Real runners retain errexit, caller state, and failure narration."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(name):
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/{name}"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts" / name).read_text()


def function(text, name, optional=False):
    match = re.search(rf"^(?P<indent>[ ]*){name}\(\) \{{\n.*?^(?P=indent)\}}$", text, re.M | re.S)
    if not match:
        if optional:
            return ""
        raise AssertionError(name)
    return match.group()


class FailureFinalization(unittest.TestCase):
    def run_case(self, runner, body, trace=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "success").mkdir()
            setup = "set -euo pipefail\n"
            setup += f"FIXTURE={shlex.quote(str(root))}\n"
            setup += r'''
SKIPPING=false
SKIP=false
START_AT=""
STOP_AFTER=""
IGOS_START_AT=""
IGOS_STOP_AFTER=""
CURRENT_PHASE=""
STOP_FILE="$FIXTURE/no-stop"
PHASE_FILE="$FIXTURE/phase"
BUILD_START=$(/usr/bin/date +%s)
BUILD_USER=fixture
CHECKPOINT=false
IGOS_MARK_OK=OK
PHASES=(core config)
CHANGED=before
_CH8_ACTIVE_PACKAGE=""
_CE_ACTIVE_PACKAGE=""
_CH8_TIER_START_MS=$(/usr/bin/date +%s%3N)
_CE_TIER_START_MS=$_CH8_TIER_START_MS
log() { printf '%s\n' "$*"; }
trace_event() { printf 'TRACE:%s\n' "$*"; }
trace_phase_enter() { printf 'ENTER:%s\n' "$*"; }
trace_phase_exit() { printf 'PHASE_EXIT:%s:%s\n' "$1" "$2"; }
trace_close() { printf 'TRACE_CLOSED\n'; }
build_failure_emit() { printf 'FAILURE:%s\n' "$*"; }
emit_build_summary() { printf 'SUMMARY:%s\n' "$*"; }
igos_progress_begin() { printf 'BEGIN:%s\n' "$1"; }
igos_progress_end() { printf 'PACKAGE_EXIT:%s:%s\n' "$1" "$2"; }
'''
            setup += "IGOS_TRACE_LIB_LOADED=" + ("1" if trace else "0") + "\n"
            if runner == "phase":
                text = source("build-intergenos.sh")
                setup += function(text, "report_phase_failure", optional=True) + "\n"
                setup += function(text, "phase_failure_exit", optional=True) + "\n"
                setup += function(text, "cleanup") + "\n"
                setup += 'SCRIPTS="$FIXTURE/absent"\npkill() { :; }\ntrap cleanup SIGTERM\n'
                setup += function(text, "run_phase") + "\n"
                setup += "fixture_phase() {\n" + body + "\n}\n"
                setup += 'run_phase core "fixture phase" fixture_phase\n'
            else:
                filename, builder, finalizer = {
                    "core": ("chroot-build-ch8.sh", "build_ch8_package", "_ch8_trace_exit"),
                    "core-extra": ("chroot-build-core-extra.sh", "build_core_package", "_ce_trace_exit"),
                }[runner]
                text = source(filename)
                setup += function(text, finalizer) + "\n"
                setup += "trap " + finalizer + " EXIT\n"
                setup += function(text, "run_package") + "\n"
                setup += builder + "() {\n" + body + "\n}\n"
                setup += 'run_package sample-dir sample 1 unused description\n'
            setup += 'printf "CONTINUED:%s:%s\\n" "$CHANGED" "$PWD"\n'
            script = root / "runner.sh"
            script.write_text(setup)
            return subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=root,
                capture_output=True, text=True, timeout=10,
            )

    def test_failures_keep_status_and_cannot_run_the_tail(self):
        failures = (
            (23, '/usr/bin/bash -c "exit 23"\nprintf "BAD_TAIL\\n"'),
            (31, 'return 31'),
            (47, 'exit 47'),
        )
        for runner in ("phase", "core", "core-extra"):
            for trace in (False, True):
                for rc, body in failures:
                    with self.subTest(runner=runner, trace=trace, rc=rc):
                        result = self.run_case(runner, body, trace)
                        output = result.stdout + result.stderr
                        self.assertEqual(result.returncode, rc, output)
                        self.assertNotIn("BAD_TAIL", output)
                        self.assertNotIn("CONTINUED", output)
                        if runner == "phase":
                            self.assertIn(f"PHASE_EXIT:core:{rc}", output)
                            self.assertIn("error: phase failed: core", output)
                        else:
                            self.assertIn(f"PACKAGE_EXIT:sample:{rc}", output)
                            self.assertIn(f"error: {runner} package failed: sample (exit {rc})", output)
                            if trace:
                                self.assertIn("TRACE:tier_end", output)

    def test_success_keeps_parent_shell_state(self):
        for runner in ("phase", "core", "core-extra"):
            with self.subTest(runner=runner):
                result = self.run_case(runner, 'CHANGED=after\ncd "$FIXTURE/success"')
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                self.assertIn("CONTINUED:after:", output)
                self.assertIn("/success", output)
                self.assertNotIn("failed:", output)

    def test_reporting_failure_preserves_the_primary_status(self):
        result = self.run_case("phase", 'trace_close() { return 79; }\nreturn 23')
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("failure reporting also failed", result.stderr)

    def test_signal_keeps_the_interruption_handler(self):
        result = self.run_case("phase", 'kill -TERM "$$"\nprintf "BAD_TAIL\\n"')
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 130, output)
        self.assertIn("build interrupted during phase: core", output)
        self.assertNotIn("error: phase failed:", output)
        self.assertNotIn("BAD_TAIL", output)

    def test_successful_explicit_exit_is_not_a_failure(self):
        for runner in ("phase", "core", "core-extra"):
            with self.subTest(runner=runner):
                result = self.run_case(runner, "exit 0")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("failed:", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

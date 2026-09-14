# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Exercise the real ISO logging boundary, including asynchronous completion."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source():
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/build-iso.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts/build-iso.sh").read_text()


class IsoLogger(unittest.TestCase):
    def run_logger(self, mode, primary=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "logger-finished"
            log = Path("/dev/full") if mode == "full" else root / "build.log"
            text = source()
            start = text.index('# Tee subsequent stdout+stderr')
            end = text.index('# Source the forensic-trace bash companion', start)
            staging = text[text.index('STAGING=$(mktemp'):text.index('ESP_TREE=')]
            setup = "set -euo pipefail\n"
            for key, value in {"LOG_FILE": log, "MARKER": marker, "MODE": mode, "TMPDIR": root}.items():
                setup += f"export {key}={shlex.quote(str(value))}\n"
            setup += r'''
tee() {
    local rc=0
    /usr/bin/tee "$@" || rc=$?
    /usr/bin/sleep 0.05
    : > "$MARKER"
    if [ "$MODE" = failed ]; then return 73; fi
    return "$rc"
}
'''
            script = root / "logger.sh"
            script.write_text(setup + text[start:end] + staging +
                              "printf 'payload\\n'\nprintf 'diagnostic\\n' >&2\n" +
                              f"exit {primary}\n")
            observer = root / "observer.sh"
            observer.write_text(
                'set +e\n/usr/bin/bash "$1"\nrc=$?\n'
                'if [ -f "$2" ]; then printf "PARENT_FINISHED:yes\\n"; '
                'else printf "PARENT_FINISHED:no\\n"; fi\nexit "$rc"\n')
            result = subprocess.run(
                ["/usr/bin/bash", str(observer), str(script), str(marker)], cwd=root,
                capture_output=True, text=True, timeout=10,
            )
            content = log.read_text() if mode != "full" else None
            leftovers = list(root.glob("build-iso-*"))
            return result, content, leftovers

    def test_success_waits_for_complete_log(self):
        result, content, leftovers = self.run_logger("success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PARENT_FINISHED:yes", result.stdout)
        self.assertEqual(content, "payload\ndiagnostic\n")
        self.assertEqual(leftovers, [])

    def test_logger_failure_becomes_the_result(self):
        result, _, leftovers = self.run_logger("failed")
        self.assertEqual(result.returncode, 73, result.stdout + result.stderr)
        self.assertIn("ISO log writer failed (exit 73)", result.stderr)
        self.assertIn("PARENT_FINISHED:yes", result.stdout)
        self.assertEqual(leftovers, [])

    def test_logger_failure_does_not_replace_primary_failure(self):
        result, _, leftovers = self.run_logger("failed", primary=52)
        self.assertEqual(result.returncode, 52, result.stdout + result.stderr)
        self.assertIn("ISO log writer failed (exit 73)", result.stderr)
        self.assertIn("PARENT_FINISHED:yes", result.stdout)
        self.assertEqual(leftovers, [])

    def test_real_tee_write_error_is_propagated(self):
        result, _, leftovers = self.run_logger("full")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ISO log writer failed (exit 1)", result.stderr)
        self.assertIn("No space left on device", result.stderr)
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()

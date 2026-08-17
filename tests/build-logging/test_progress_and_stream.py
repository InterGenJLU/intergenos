# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Runs the build-observability shell tests inside the Python suite.

The shell half lives in test_progress_and_stream.sh, written in the same shape
as the repo's other shell tests, because what it checks is shell: the tier
scripts' own log() definitions and the progress helpers they call.

This wrapper exists because those other shell tests are NOT reached by anything
automatic — not by this suite, not by the git hooks — so they run only when
somebody remembers to run them. A test nobody runs is not evidence. Rather than
change how the existing ones are invoked (which would put eight previously
unrun files into the suite in one step, unmeasured), this covers the one added
here; adopting a general runner for the rest is worth doing separately, once
somebody has confirmed they pass.
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELL_TEST = Path(__file__).with_suffix(".sh")


class TestProgressAndStreamShell(unittest.TestCase):
    def test_shell_suite_passes(self):
        self.assertTrue(SHELL_TEST.is_file(), str(SHELL_TEST))
        proc = subprocess.run(
            ["bash", str(SHELL_TEST)],
            capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT),
        )
        output = proc.stdout + proc.stderr
        # The whole output is attached on failure: a shell test that reports
        # "FAIL: <name>" is useless if the name is swallowed by the assertion.
        self.assertEqual(proc.returncode, 0, f"shell tests failed:\n{output}")
        self.assertIn("FAIL=0", output, output)

    def test_shell_suite_emits_no_warnings(self):
        # The first version of the shell test made grep complain ("stray \
        # before ..."). A warning coming out of the instrument hides the
        # signal it exists to carry, so the instrument is held to the same
        # bar as the code it checks.
        proc = subprocess.run(
            ["bash", str(SHELL_TEST)],
            capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT),
        )
        combined = (proc.stdout + proc.stderr).lower()
        for noise in ("warning", "stray", "syntax error", "unbound variable"):
            self.assertNotIn(noise, combined, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

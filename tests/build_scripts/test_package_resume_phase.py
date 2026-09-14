# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Execute the orchestrator's actual phase-name and package-resume validation."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPPORTED = {"core", "core-extra", "base"}


def source():
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/build-intergenos.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts/build-intergenos.sh").read_text()


class PackageResumePhase(unittest.TestCase):
    def run_validation(self, phase, package="sample"):
        text = source()
        phases = re.search(r"^PHASES=\(\n.*?^\)", text, re.M | re.S).group()
        validator = re.search(r"^validate_phase_name\(\) \{\n.*?^\}", text, re.M | re.S).group()
        start = text.index('validate_phase_name "$START_AT"')
        end = text.index('# Conditionally enable publish phase', start)
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "validate.sh"
            script.write_text(
                'set -euo pipefail\n' + phases + '\n' + validator + '\n' +
                'STOP_AFTER=""\nSTART_AT=' + shlex.quote(phase) + '\n' +
                'START_AT_PKG=' + shlex.quote(package) + '\n' +
                text[start:end] + "printf 'VALIDATED\\n'\n")
            return subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=tmp,
                capture_output=True, text=True, timeout=10,
            )

    def test_unsupported_phase_is_refused(self):
        phases = re.search(r"^PHASES=\(\n(.*?)^\)", source(), re.M | re.S).group(1).split()
        for phase in phases:
            if phase in SUPPORTED:
                continue
            with self.subTest(phase=phase):
                result = self.run_validation(phase)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 2, output)
                self.assertIn("--start-at-pkg", output)
                self.assertIn(phase, output)
                self.assertNotIn("VALIDATED", output)

    def test_supported_phase_accepts_package_resume(self):
        for phase in SUPPORTED:
            with self.subTest(phase=phase):
                result = self.run_validation(phase)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("VALIDATED", result.stdout)

    def test_without_package_resume_all_phases_remain_valid(self):
        phases = re.search(r"^PHASES=\(\n(.*?)^\)", source(), re.M | re.S).group(1).split()
        for phase in phases:
            with self.subTest(phase=phase):
                result = self.run_validation(phase, package="")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_package_resume_still_requires_a_phase(self):
        result = self.run_validation("")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --start-at", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

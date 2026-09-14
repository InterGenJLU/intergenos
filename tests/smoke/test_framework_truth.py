# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed controls for the smoke orchestrator and JSON output."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_DIR = REPO_ROOT / "installer" / "smoke"
SMOKE_TEST = SMOKE_DIR / "smoke-test.sh"
LIB_SH = SMOKE_DIR / "lib.sh"


class FrameworkTruthTests(unittest.TestCase):
    def test_missing_required_check_module_aborts_instead_of_green_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "smoke"
            checks = staged / "checks"
            checks.mkdir(parents=True)
            shutil.copy2(SMOKE_TEST, staged / "smoke-test.sh")
            shutil.copy2(LIB_SH, staged / "lib.sh")

            functions = {
                "signing": "run_signing_checks",
                "boot": "run_boot_checks",
                "services": "run_services_checks",
                "gaming": "run_gaming_checks",
                "chronicle": "run_chronicle_checks",
                "capture": "run_capture_checks",
                "hardware": "run_hardware_checks",
            }
            for name, function in functions.items():
                (checks / f"{name}.sh").write_text(f"{function}() {{ :; }}\n")
            # checks/pkm.sh is deliberately absent. On the old orchestrator the
            # failed source and undefined runner were followed by a zero-failure
            # summary and exit 0.
            result = subprocess.run(
                ["/usr/bin/bash", str(staged / "smoke-test.sh")],
                capture_output=True,
                text=True,
                env=dict(os.environ),
                timeout=30,
                check=False,
            )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("required check module", result.stderr)
        self.assertIn("checks/pkm.sh", result.stderr)

    def test_json_messages_escape_control_characters_and_round_trip(self):
        script = textwrap.dedent(
            f"""
            set -uo pipefail
            SMOKE_JSON=1
            . "{LIB_SH}"
            check_warn sample $'line one\\nline two\\tcolumn\\rreturn\\x01unit'
            summary
            """
        )
        result = subprocess.run(
            ["/usr/bin/bash", "-c", script],
            capture_output=True,
            text=True,
            env=dict(os.environ),
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = json.loads(result.stdout)
        message = parsed["checks"][0]["message"]
        self.assertEqual(message, "line one\nline two\tcolumn\rreturn\x01unit")


if __name__ == "__main__":
    unittest.main()

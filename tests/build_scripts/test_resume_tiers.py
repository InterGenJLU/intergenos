# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Derive resume acquisition from the actual phase invocation order."""

import os
import re
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
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/build-intergenos.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts/build-intergenos.sh").read_text()


class ResumeTiers(unittest.TestCase):
    def run_selector(self, phase, change_plan=None, staging=False):
        text = source()
        plan = re.findall(r'^\s*run_phase "[^\n]+$', text, re.M)
        self.assertTrue(plan)
        if change_plan == "move-compute":
            compute = next(line for line in plan if 'run_phase "compute"' in line)
            plan.remove(compute)
            index = next(i for i, line in enumerate(plan) if 'run_phase "desktop"' in line)
            plan.insert(index, compute)
        elif change_plan == "unknown-phase":
            plan.append('run_phase "new-package-phase" "new phase" phase_new')
        names = ["tiers_for_start_at"] + (["ensure_sources_staged"] if staging else [])
        functions = []
        for name in names:
            match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.M | re.S)
            self.assertIsNotNone(match)
            functions.append(match.group())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "selector.sh"
            setup = "set -euo pipefail\nSTART_AT=" + shlex.quote(phase) + "\n"
            for variable in ("SCRIPTS", "SOURCES", "PATCHES", "PACKAGES_DIR", "IGOS"):
                setup += f"{variable}={shlex.quote(str(root / 'absent'))}\n"
            setup += f"BUILD_LOG={shlex.quote(str(root / 'log'))}\n"
            setup += 'log() { printf "%s\\n" "$*"; }\n'
            setup += 'python3() { printf "UNEXPECTED_ACQUISITION\\n" >&2; return 91; }\n'
            call = "ensure_sources_staged" if staging else "tiers_for_start_at"
            script.write_text(setup + "\n".join(functions) + f"\n{call}\nexit $?\n" + "\n".join(plan) + "\n")
            return subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=root,
                capture_output=True, text=True, timeout=10,
            )

    def test_current_and_later_package_tiers(self):
        expected = {
            "core": "core base desktop extra compute ai",
            "core-extra": "core base desktop extra compute ai",
            "base": "base core desktop extra compute ai",
            "kernel": "core desktop extra compute ai",
            "desktop": "desktop extra compute ai",
            "extra": "extra compute ai",
            "compute": "compute ai",
            "ai": "ai",
            "toolchain": "toolchain core base desktop extra compute ai",
            "chroot-tools": "toolchain core base desktop extra compute ai",
        }
        for phase, tiers in expected.items():
            with self.subTest(phase=phase):
                result = self.run_selector(phase)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.split(),
                                 [part for tier in tiers.split() for part in ("--tier", tier)])

    def test_full_entry_requests_all_sources(self):
        for phase in ("", "validate", "verify-sources", "setup"):
            with self.subTest(phase=phase):
                result = self.run_selector(phase)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "--all")

    def test_post_package_entry_does_not_start_acquisition(self):
        for phase in ("bootloader", "image", "manifest", "squashfs", "ukis-verity", "iso", "publish"):
            with self.subTest(phase=phase):
                result = self.run_selector(phase, staging=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("UNEXPECTED_ACQUISITION", result.stderr)

    def test_order_follows_invocations_when_the_plan_changes(self):
        result = self.run_selector("compute", change_plan="move-compute")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(),
                         "--tier compute --tier desktop --tier extra --tier ai".split())

    def test_unknown_phase_or_mapping_fails_before_acquisition(self):
        for phase, change in (("unknown", None), ("core", "unknown-phase")):
            with self.subTest(phase=phase, change=change):
                result = self.run_selector(phase, change_plan=change, staging=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn("UNEXPECTED_ACQUISITION", result.stderr)


if __name__ == "__main__":
    unittest.main()

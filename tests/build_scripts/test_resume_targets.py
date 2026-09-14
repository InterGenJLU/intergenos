# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run each driver's entry point with setup stopped at its helper boundary."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRIVERS = ("chroot-build-ch8.sh", "chroot-build-core-extra.sh")


def source(name):
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/{name}"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts" / name).read_text()


class ResumeTargets(unittest.TestCase):
    def run_driver(self, name, target):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            driver = root / name
            driver.write_text(source(name))
            startup = root / "startup.sh"
            # Stop before loading package helpers or building any package.
            # These shell functions also keep the original entry point from
            # creating its hardcoded log directory before it reaches source.
            startup.write_text(
                "mkdir() { printf 'SETUP_STARTED\\n'; }\n"
                "source() { printf 'HELPER_BOUNDARY\\n'; exit 93; }\n"
            )
            env = {k: v for k, v in os.environ.items()
                   if k not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")}
            env.update(BASH_ENV=str(startup), IGOS_START_AT=target)
            return subprocess.run(
                ["/usr/bin/bash", str(driver)], env=env, cwd=root,
                capture_output=True, text=True, timeout=10,
            )

    def test_unknown_target_refused_before_setup(self):
        for name in DRIVERS:
            for target in ("missing-package", ".*", "[", "", r"\147cc-core"):
                if not target:
                    continue
                with self.subTest(driver=name, target=target):
                    result = self.run_driver(name, target)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn(target, result.stderr)
                    self.assertNotIn("SETUP_STARTED", result.stdout)
                    self.assertNotIn("HELPER_BOUNDARY", result.stdout)

    def test_every_plan_name_and_alias_is_accepted(self):
        for name in DRIVERS:
            pairs = re.findall(r'^run_package "([^"]+)" "([^"]+)"', source(name), re.M)
            self.assertTrue(pairs)
            for target in sorted({word for pair in pairs for word in pair}):
                with self.subTest(driver=name, target=target):
                    result = self.run_driver(name, target)
                    self.assertEqual(result.returncode, 93, result.stdout + result.stderr)
                    self.assertIn("HELPER_BOUNDARY", result.stdout)

    def test_no_resume_retains_normal_setup(self):
        for name in DRIVERS:
            with self.subTest(driver=name):
                result = self.run_driver(name, "")
                self.assertEqual(result.returncode, 93, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

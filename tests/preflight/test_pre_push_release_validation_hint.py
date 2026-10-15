# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The pre-push refusal of a promotion with no record prints a launch that works.

WHY THIS EXISTS. A push to master with RELEASE_VALIDATION_RECORD unset is
refused, and the refusal tells the operator how to produce a record. It printed
an unprivileged launch of the record runner. Launched without root, the runner
cannot measure the root tier: its gates skip, and the release gate refuses the
record for the undeclared skip, so following the hint could never end in a
promotion. The hint now prints the form that produces a record the release gate
accepts: the commit exported into a directory only root can write, and the
runner launched through sudo from the account the gates measure.

HOW. A real push reaches this block only after every gate before it has passed,
which needs the whole tree and minutes per push. So the block is cut from the
real hook at its own guard and run by bash: what is asserted is what the hook
prints when the record is missing. The gates before it are not exercised here;
their own tests cover them. The printed form itself is proven by firing it: the
record runner's real root launch follows it.
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRE_PUSH = REPO_ROOT / ".githooks" / "pre-push"
GUARD = '    if [ -z "${RELEASE_VALIDATION_RECORD:-}" ]; then\n'
END = "\n    fi\n"


def _missing_record_block() -> str:
    text = PRE_PUSH.read_text(encoding="utf-8")
    start = text.index(GUARD)
    return text[start:text.index(END, start) + len(END)]


class TheMissingRecordRefusalPrintsTheRootLaunch(unittest.TestCase):

    def setUp(self) -> None:
        self.assertEqual(PRE_PUSH.read_text(encoding="utf-8").count(GUARD), 1,
                         "the hook's missing-record guard is not where this "
                         "test cuts the block it runs")
        self.run_ = subprocess.run(
            ["bash", "-c", _missing_record_block()], capture_output=True,
            text=True, timeout=60,
            env={"PATH": os.environ.get("PATH", os.defpath)})
        self.said = self.run_.stdout + self.run_.stderr

    def test_a_promotion_with_no_record_is_still_refused(self) -> None:
        """The control: the block still refuses, so the hint is the only change."""
        self.assertEqual(self.run_.returncode, 1, self.said)
        self.assertIn("BLOCK: RELEASE_VALIDATION_RECORD is not set", self.said)
        self.assertIn("A missing record is NOT VALIDATED", self.said)

    def test_the_hint_exports_the_commit_into_a_directory_only_root_can_write(self) -> None:
        self.assertIn("git -c tar.umask=0022 archive -o <tree>.tar <commit>",
                      self.said)
        self.assertRegex(self.said, r"sudo mkdir -m 0755 \S+",
                         "the export directory is not made by root, fresh")
        self.assertRegex(self.said, r"sudo tar -x -f <tree>\.tar -C \S+")

    def test_the_hint_launches_the_runner_through_sudo_from_that_directory(self) -> None:
        export = re.search(r"sudo tar -x -f <tree>\.tar -C (\S+)", self.said)
        self.assertIsNotNone(export, self.said)
        tree = export.group(1)
        self.assertIn(f"sudo python3 {tree}/scripts/run-installed-gates.py",
                      self.said)
        self.assertIn(f"--tier {tree}/tests/installed", self.said)

    def test_the_hint_no_longer_prints_the_unprivileged_launch(self) -> None:
        launches = [line.strip() for line in self.said.splitlines()
                    if "scripts/run-installed-gates.py" in line]
        self.assertTrue(launches, self.said)
        for line in launches:
            self.assertTrue(line.startswith("sudo python3 "),
                            f"an unprivileged launch is still printed: {line}")


if __name__ == "__main__":
    unittest.main()

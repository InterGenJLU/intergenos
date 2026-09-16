# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""R001.3 gating row 32 — a finished install can be rolled back to itself.

The row's wording is that a machine finishes installing with its newest
restore point older than the system it is running. Measured on an
installed R001.2-03 machine, the true shape is one notch worse: that
machine's restore-point history begins an hour and forty minutes AFTER
the install, and every entry in it was taken by a package transaction.
Nothing ever captured the system as installed, so the oldest state the
person can return to is whatever the machine happened to be in when they
first installed a package — and on a machine where nobody installs
anything, there is no restore point at all.

The install now takes one of its own before it unmounts the target. It is
best effort: the backup utility is an optional package, and a machine
that finishes installing is not failed over a restore point. A failure is
named as a warning rather than swallowed, because a person who believes
they can roll back and cannot is worse off than one who was told.
"""

import unittest
from unittest.mock import patch

from installer.backend import restorepoint


class TestTheInstallTakesItsOwnRestorePoint(unittest.TestCase):

    def _run(self, rc_capture=0, out_capture='{"version_id": "0000000001-abc"}',
             err_capture="", exists=True, rc_list=0,
             out_list='[{"version_id": "0000000001-abc", "reason": "the system as installed"}]'):
        calls = []

        def fake_run(target, command):
            calls.append(command)
            if " capture " in command:
                return (rc_capture, out_capture, err_capture)
            return (rc_list, out_list, "")

        with patch.object(restorepoint.trace, "traced_run_chroot",
                          side_effect=fake_run), \
             patch.object(restorepoint.os.path, "exists", return_value=exists):
            result = restorepoint.take_install_restore_point("/mnt/target")
        return result, calls

    def test_a_restore_point_is_captured_and_read_back(self):
        result, calls = self._run()
        self.assertEqual(result["status"], "taken")
        self.assertEqual(result["version_id"], "0000000001-abc")
        self.assertIn(f"capture {restorepoint.LAYER}", calls[0])
        self.assertIn(restorepoint.INSTALL_REASON, calls[0])
        self.assertIn(f"list {restorepoint.LAYER}", calls[1],
                      "the capture must be read back, not assumed")

    def test_the_utility_not_being_installed_is_a_skip_not_a_failure(self):
        result, calls = self._run(exists=False)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(calls, [])
        self.assertIn("not installed", result["detail"])

    def test_a_refused_capture_is_reported_with_what_the_tool_said(self):
        result, _ = self._run(rc_capture=1, out_capture="",
                              err_capture="no space left on device")
        self.assertEqual(result["status"], "failed")
        self.assertIn("no space left on device", result["detail"])

    def test_a_capture_that_cannot_be_read_back_is_not_called_taken(self):
        """A zero exit status is the tool's claim, not the machine's state."""
        result, _ = self._run(out_list="[]")
        self.assertEqual(result["status"], "failed")
        self.assertIn("read back", result["detail"])

    def test_unreadable_output_is_a_failure_rather_than_an_exception(self):
        result, _ = self._run(out_capture="not json at all", out_list="also not json")
        self.assertEqual(result["status"], "failed")


class TestTheInstallCallsItBeforeItUnmounts(unittest.TestCase):

    def test_the_cleanup_phase_takes_the_restore_point_before_unmounting(self):
        from pathlib import Path
        from installer.backend import install as install_mod
        src = Path(install_mod.__file__).read_text()
        self.assertIn("restorepoint.take_install_restore_point(target)", src)
        self.assertLess(
            src.index("restorepoint.take_install_restore_point(target)"),
            src.index('_emit(PHASE_CLEANUP, 12, "unmounting target")'),
            "a restore point taken after the unmount would have nothing to read")


if __name__ == "__main__":
    unittest.main()

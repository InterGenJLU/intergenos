# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for installer/backend/hooks.py:run_post_install_hooks visibility.

A hook's return code, stdout and stderr were previously captured into
variables and dropped — no log line, no trace event, no per-hook record
anywhere. These tests pin the corrected contract: every hook execution
routes through trace.traced_run_chroot (which emits the subprocess trace
pair with rc/stdout/stderr), a failing hook produces a WARNING log record
carrying rc and both streams, hook failures stay NON-fatal, and the run
ends with a recipe_hooks_summary trace event naming the failures.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.backend.hooks import run_post_install_hooks


def _make_package(packages_dir, tier, name, version="1.0"):
    pkg = Path(packages_dir) / tier / name
    pkg.mkdir(parents=True)
    (pkg / "build.sh").write_text(
        "post_install() {\n    true\n}\n"
    )
    (pkg / "package.yml").write_text(f"version: {version}\n")
    return pkg


class TestHookVisibility(unittest.TestCase):
    def setUp(self):
        self.target = tempfile.mkdtemp()
        (Path(self.target) / "tmp").mkdir()
        self.packages_dir = tempfile.mkdtemp()
        _make_package(self.packages_dir, "core", "alpha")
        _make_package(self.packages_dir, "desktop", "beta")

    def tearDown(self):
        shutil.rmtree(self.target)
        shutil.rmtree(self.packages_dir)

    @patch("installer.backend.hooks.trace.trace_event")
    @patch("installer.backend.hooks.trace.traced_run_chroot")
    def test_success_routes_through_traced_run_chroot(
        self, mock_chroot, mock_event
    ):
        mock_chroot.return_value = (0, "ok", "")
        executed = run_post_install_hooks(self.target, self.packages_dir)
        self.assertEqual(executed, 2)
        self.assertEqual(mock_chroot.call_count, 2)
        for call, name in zip(mock_chroot.call_args_list, ("alpha", "beta")):
            self.assertEqual(call.kwargs["phase"], "hooks")
            self.assertEqual(call.kwargs["pkg"], name)
        mock_event.assert_called_once_with(
            "recipe_hooks_summary", total=2, executed=2, failed=[],
        )

    @patch("installer.backend.hooks.trace.trace_event")
    @patch("installer.backend.hooks.trace.traced_run_chroot")
    def test_failing_hook_is_logged_nonfatal_and_named_in_summary(
        self, mock_chroot, mock_event
    ):
        mock_chroot.side_effect = [
            (1, "partial output", "boom: no such service"),
            (0, "", ""),
        ]
        with self.assertLogs("installer.backend.hooks", level="WARNING") as cm:
            executed = run_post_install_hooks(self.target, self.packages_dir)
        self.assertEqual(executed, 1)
        warning = "\n".join(cm.output)
        self.assertIn("alpha", warning)
        self.assertIn("exited 1", warning)
        self.assertIn("partial output", warning)
        self.assertIn("boom: no such service", warning)
        mock_event.assert_called_once_with(
            "recipe_hooks_summary", total=2, executed=1, failed=["alpha"],
        )

    @patch("installer.backend.hooks.trace.trace_event")
    @patch("shutil.copytree")
    def test_copytree_failure_emits_skip_event(self, mock_copy, mock_event):
        mock_copy.side_effect = OSError("disk full")
        executed = run_post_install_hooks(self.target, self.packages_dir)
        self.assertEqual(executed, 0)
        mock_event.assert_called_once_with(
            "recipe_hooks_skipped", total=2, reason="disk full",
        )


if __name__ == "__main__":
    unittest.main()

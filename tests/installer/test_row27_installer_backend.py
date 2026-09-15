"""R001.3 gating row 27 — four installer-backend correctness defects.

Each test names the defect it guards and fails on the tree before the fix:

1. the final cleanup deleted the chosen login when it spelled ``tester``
   (``users.remove_test_accounts`` decided by name alone);
3. ``disks.unmount_target`` discarded every umount result, and the durable
   trace sink on the target stayed open through the unmount;
4. ``disks.mount_target`` left the root mounted when the ESP step failed;
5. ``packages.install_packages`` returned the whole queue as the installed
   names, so post-install hooks ran for packages whose install had failed.

Item 2 (locale modifiers) is covered by installer/tests/test_config_locale.py.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from installer.backend import disks, packages, users
from scripts.lib import igos_trace


def _completed(rc, stderr=""):
    return subprocess.CompletedProcess(args=["umount"], returncode=rc,
                                       stdout="", stderr=stderr)


class TestScrubProtectsTheChosenLogin(unittest.TestCase):
    """Item 1."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.passwd = Path(self.tmp) / "etc" / "passwd"
        self.passwd.parent.mkdir(parents=True)
        self.passwd.write_text(
            "root:x:0:0:root:/root:/bin/bash\n"
            "tester:x:1001:1001::/home/tester:/bin/bash\n")

    def _userdel(self, cmd, **kwargs):
        # Stand in for userdel: drop the named account from the fixture.
        name = cmd[-1]
        lines = [ln for ln in self.passwd.read_text().splitlines()
                 if not ln.startswith(name + ":")]
        self.passwd.write_text("\n".join(lines) + "\n")
        return _completed(0)

    def test_the_chosen_login_named_tester_is_kept(self):
        with patch.object(users.trace, "traced_run",
                          side_effect=self._userdel) as run:
            out = users.remove_test_accounts(
                self.tmp, protected_username="tester")
        run.assert_not_called()
        self.assertEqual(out["removed"], [])
        self.assertEqual(out["survivors"], [])
        self.assertEqual(out["protected"], "tester")
        self.assertIn("tester:x:1001", self.passwd.read_text())

    def test_an_inherited_tester_is_still_removed_for_another_login(self):
        with patch.object(users.trace, "traced_run",
                          side_effect=self._userdel) as run:
            out = users.remove_test_accounts(
                self.tmp, protected_username="alice")
        self.assertEqual(out["removed"], ["tester"])
        self.assertEqual(out["survivors"], [])
        self.assertEqual(run.call_args.args[0][-1], "tester")
        self.assertNotIn("tester:", self.passwd.read_text())

    def test_legacy_call_without_a_protected_name_removes_tester(self):
        with patch.object(users.trace, "traced_run",
                          side_effect=self._userdel):
            out = users.remove_test_accounts(self.tmp)
        self.assertEqual(out["removed"], ["tester"])
        self.assertIsNone(out["protected"])


class TestInstalledNamesAreTheSuccessfulOnes(unittest.TestCase):
    """Item 5."""

    @patch("installer.backend.packages.PackageInstaller")
    @patch("installer.backend.packages.PackageDB")
    @patch("installer.backend.packages.get_group_packages")
    def test_a_failed_package_is_not_an_installed_name(
            self, mock_get_packages, mock_db_cls, mock_installer_cls):
        mock_get_packages.return_value = [
            ("ok-pkg", "1.0", Path("/fake/ok-pkg.tar.gz")),
            ("bad-pkg", "1.0", Path("/fake/bad-pkg.tar.gz")),
            ("late-pkg", "1.0", Path("/fake/late-pkg.tar.gz")),
        ]
        mock_installer = MagicMock()
        mock_installer.install.side_effect = [
            (True, "ok"), (False, "archive refused"), (True, "ok")]
        mock_installer_cls.return_value = mock_installer
        tmp = tempfile.mkdtemp()

        success, fail_count, failed, installed = packages.install_packages(
            tmp, tmp, groups=["core"])

        self.assertEqual((success, fail_count), (2, 1))
        self.assertEqual(failed[0][0], "bad-pkg")
        self.assertEqual(installed, ["ok-pkg", "late-pkg"])
        # The queue every install() sees is still the whole order.
        for call in mock_installer.install.call_args_list:
            self.assertEqual(call.kwargs.get("queue"),
                             ["ok-pkg", "bad-pkg", "late-pkg"])


class TestMountTargetUnwindsOnEspFailure(unittest.TestCase):
    """Item 4."""

    def setUp(self):
        self.target = tempfile.mkdtemp()
        self.parts = {"root": "/dev/sda2", "esp": "/dev/sda1", "efi": True}

    def _cmds(self, run):
        return [c.args[0] if isinstance(c.args[0], list) else c.args[0].split()
                for c in run.call_args_list]

    def test_esp_mount_failure_unmounts_the_root_and_reraises(self):
        def run(cmd, **kw):
            text = cmd if isinstance(cmd, str) else " ".join(cmd)
            if text.startswith("mount /dev/sda1"):
                raise RuntimeError("mount: /dev/sda1: wrong fs type")
        with patch.object(disks, "_run", side_effect=run) as mock_run:
            with self.assertRaises(RuntimeError) as ctx:
                disks.mount_target(self.parts, target=self.target)
        self.assertIn("wrong fs type", str(ctx.exception))
        cmds = self._cmds(mock_run)
        self.assertEqual(cmds[0], ["mount", "/dev/sda2", self.target])
        self.assertEqual(cmds[-1], ["umount", self.target])
        self.assertEqual(len(cmds), 3)

    def test_esp_mkdir_failure_unmounts_the_root(self):
        with patch.object(disks, "_run") as mock_run, \
                patch.object(disks.os, "makedirs",
                             side_effect=[None, OSError("read-only file system")]):
            with self.assertRaises(OSError):
                disks.mount_target(self.parts, target=self.target)
        self.assertEqual(self._cmds(mock_run)[-1], ["umount", self.target])

    def test_a_failed_unwind_is_named_beside_the_original_error(self):
        def run(cmd, **kw):
            text = cmd if isinstance(cmd, str) else " ".join(cmd)
            if text.startswith("mount /dev/sda1"):
                raise RuntimeError("esp failed")
            if text.startswith("umount"):
                raise RuntimeError("target is busy")
        with patch.object(disks, "_run", side_effect=run):
            with self.assertRaises(RuntimeError) as ctx:
                disks.mount_target(self.parts, target=self.target)
        self.assertIn("esp failed", str(ctx.exception))
        self.assertIn("target is busy", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_root_mount_failure_unwinds_nothing(self):
        with patch.object(disks, "_run",
                          side_effect=RuntimeError("root failed")) as mock_run:
            with self.assertRaises(RuntimeError):
                disks.mount_target(self.parts, target=self.target)
        self.assertEqual(len(mock_run.call_args_list), 1)

    def test_bios_and_efi_success_paths_issue_only_mounts(self):
        with patch.object(disks, "_run") as mock_run:
            disks.mount_target({"root": "/dev/sda2"}, target=self.target)
            disks.mount_target(self.parts, target=self.target)
        for cmd in self._cmds(mock_run):
            self.assertEqual(cmd[0], "mount")
        self.assertEqual(len(mock_run.call_args_list), 3)


class TestUnmountTargetReportsWhatStayedMounted(unittest.TestCase):
    """Item 3 — the helper."""

    def setUp(self):
        self.target = "/mnt/target"

    def _run(self, mounted, results):
        calls = []

        mounted = set(mounted)

        def traced_run(cmd, **kw):
            calls.append(cmd[-1])
            result = results.get(cmd[-1], _completed(0))
            if result.returncode == 0:
                mounted.discard(cmd[-1])   # a clean umount changes the state
            return result

        with patch.object(disks.os.path, "ismount",
                          side_effect=lambda p: p in mounted), \
                patch.object(disks.trace, "traced_run", side_effect=traced_run), \
                patch.object(disks.trace, "trace_event"):
            failures = disks.unmount_target(self.target)
        return failures, calls

    def test_everything_unmounts_cleanly(self):
        mounted = {f"{self.target}/boot/efi", self.target}
        failures, calls = self._run(mounted, {})
        self.assertEqual(failures, [])
        self.assertEqual(calls, [f"{self.target}/boot/efi", self.target])

    def test_a_busy_target_is_reported_not_swallowed(self):
        mounted = {self.target}
        failures, calls = self._run(
            mounted, {self.target: _completed(32, "umount: /mnt/target: target is busy.")})
        self.assertEqual(len(failures), 1)
        self.assertIn("rc=32", failures[0])
        self.assertIn("target is busy", failures[0])

    def test_paths_that_are_not_mount_points_are_skipped_not_failed(self):
        failures, calls = self._run(set(), {})
        self.assertEqual(failures, [])
        self.assertEqual(calls, [])

    def test_a_zero_exit_with_the_target_still_mounted_is_a_failure(self):
        state = {"n": 0}

        def ismount(p):
            # The target reads as mounted before AND after the umount.
            return p == self.target

        with patch.object(disks.os.path, "ismount", side_effect=ismount), \
                patch.object(disks.trace, "traced_run",
                             return_value=_completed(0)), \
                patch.object(disks.trace, "trace_event"):
            failures = disks.unmount_target(self.target)
        self.assertEqual(len(failures), 1)
        self.assertIn("still a mount point", failures[0])


class TestDetachTargetSink(unittest.TestCase):
    """Item 3 — the trace library closes the on-target copy before the unmount."""

    def setUp(self):
        self.saved = list(igos_trace._SINKS)
        self.tmp = Path(tempfile.mkdtemp())
        self.target = self.tmp / "target"
        (self.target / "var" / "log").mkdir(parents=True)
        self.live = igos_trace._open_600(self.tmp / "live.log")
        self.on_target = igos_trace._open_600(
            self.target / "var" / "log" / "forge-install-x.log")
        igos_trace._SINKS[:] = [self.live, self.on_target]

    def tearDown(self):
        for h in (self.live, self.on_target):
            try:
                h.close()
            except Exception:
                pass
        igos_trace._SINKS[:] = self.saved

    def test_only_the_sink_under_the_target_is_closed(self):
        closed = igos_trace.detach_target_sink(self.target)
        self.assertEqual(closed, 1)
        self.assertTrue(self.on_target.closed)
        self.assertFalse(self.live.closed)
        self.assertEqual(igos_trace._SINKS, [self.live])
        last = (self.target / "var" / "log" / "forge-install-x.log"
                ).read_text().splitlines()[-1]
        self.assertIn('"type": "target_sink_detached"', last)

    def test_no_target_sink_is_a_no_op(self):
        igos_trace._SINKS[:] = [self.live]
        self.assertEqual(igos_trace.detach_target_sink(self.target), 0)
        self.assertFalse(self.live.closed)


if __name__ == "__main__":
    unittest.main()

"""Tests for installer/backend/install.py preflight_* functions — C-021 extended.

Covers:
  - preflight_check_binaries: always-set + LUKS conditional + missing-list message
  - preflight_check_archive_availability: empty-group detection + per-group message +
    no-op when package_groups absent/empty

Both functions called from PHASE_VALIDATE in run_install (before PHASE_PARTITION
destructive write). The PHASE_VALIDATE integration is exercised indirectly by
test_install_orchestrator.py's existing happy-path tests via mocked backend
modules; these tests focus on the pre-flight functions in isolation.
"""

import unittest
from unittest.mock import patch

from installer.backend.install import (
    PREFLIGHT_LIVE_BINARIES_ALWAYS,
    PREFLIGHT_LIVE_BINARIES_LUKS,
    preflight_check_archive_availability,
    preflight_check_binaries,
)


class TestPreflightCheckBinaries(unittest.TestCase):
    @patch("installer.backend.install.shutil.which")
    def test_all_present_no_raise(self, mock_which):
        # Every binary lookup returns a fake path → no missing.
        mock_which.return_value = "/usr/bin/fake"
        preflight_check_binaries({})  # no LUKS

    @patch("installer.backend.install.shutil.which")
    def test_single_missing_raises_with_name(self, mock_which):
        # First binary missing, rest present.
        def which_side_effect(name):
            return None if name == "parted" else "/usr/bin/fake"
        mock_which.side_effect = which_side_effect
        with self.assertRaises(RuntimeError) as ctx:
            preflight_check_binaries({})
        self.assertIn("parted", str(ctx.exception))
        self.assertIn("live-ISO is missing", str(ctx.exception))

    @patch("installer.backend.install.shutil.which")
    def test_multiple_missing_lists_all_sorted(self, mock_which):
        # parted + blkid missing, rest present.
        missing_set = {"parted", "blkid"}
        mock_which.side_effect = lambda name: None if name in missing_set else "/usr/bin/fake"
        with self.assertRaises(RuntimeError) as ctx:
            preflight_check_binaries({})
        msg = str(ctx.exception)
        self.assertIn("blkid", msg)
        self.assertIn("parted", msg)
        # Sorted output: blkid before parted (alphabetical).
        self.assertLess(msg.index("blkid"), msg.index("parted"))

    @patch("installer.backend.install.shutil.which")
    def test_luks_enabled_adds_cryptsetup_to_required(self, mock_which):
        # All always-set present; cryptsetup missing → raise.
        mock_which.side_effect = (
            lambda name: None if name == "cryptsetup" else "/usr/bin/fake"
        )
        with self.assertRaises(RuntimeError) as ctx:
            preflight_check_binaries({"luks_enabled": True})
        self.assertIn("cryptsetup", str(ctx.exception))

    @patch("installer.backend.install.shutil.which")
    def test_luks_disabled_does_not_require_cryptsetup(self, mock_which):
        # cryptsetup missing but luks_enabled=False → no raise.
        mock_which.side_effect = (
            lambda name: None if name == "cryptsetup" else "/usr/bin/fake"
        )
        preflight_check_binaries({})  # luks_enabled absent = falsy
        preflight_check_binaries({"luks_enabled": False})


class TestPreflightCheckArchiveAvailability(unittest.TestCase):
    @patch("installer.backend.install.packages.get_group_packages")
    def test_all_groups_resolve_no_raise(self, mock_get):
        mock_get.return_value = [("pkg1", "1.0", "/path/pkg1.tar.gz")]
        preflight_check_archive_availability(
            {"package_groups": ["core", "base"]}, "/archive", "/packages"
        )
        # get_group_packages called once per group.
        self.assertEqual(mock_get.call_count, 2)

    @patch("installer.backend.install.packages.get_group_packages")
    def test_one_empty_group_raises_with_name(self, mock_get):
        def get_side_effect(groups, archive_dir, packages_dir):
            return [] if groups == ["ai"] else [("x", "1", "/p")]
        mock_get.side_effect = get_side_effect
        with self.assertRaises(RuntimeError) as ctx:
            preflight_check_archive_availability(
                {"package_groups": ["core", "ai"]}, "/archive", "/packages"
            )
        msg = str(ctx.exception)
        self.assertIn("ai", msg)
        self.assertIn("zero archives", msg)

    @patch("installer.backend.install.packages.get_group_packages")
    def test_multiple_empty_groups_lists_all(self, mock_get):
        mock_get.return_value = []  # every group empty
        with self.assertRaises(RuntimeError) as ctx:
            preflight_check_archive_availability(
                {"package_groups": ["core", "base", "desktop-gnome"]},
                "/archive", "/packages",
            )
        msg = str(ctx.exception)
        for g in ("core", "base", "desktop-gnome"):
            self.assertIn(g, msg)

    @patch("installer.backend.install.packages.get_group_packages")
    def test_empty_package_groups_in_cfg_no_raise(self, mock_get):
        # Legitimate no-op (no groups requested).
        preflight_check_archive_availability(
            {"package_groups": []}, "/archive", "/packages"
        )
        mock_get.assert_not_called()

    @patch("installer.backend.install.packages.get_group_packages")
    def test_missing_package_groups_key_no_raise(self, mock_get):
        # cfg without package_groups key → defaults to empty list.
        preflight_check_archive_availability({}, "/archive", "/packages")
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()

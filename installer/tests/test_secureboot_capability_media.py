# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Unit coverage for the completion-screen guidance probes.

Two probes feed the conditional Secure-Boot guidance on both completion
screens (GUI Done page + TUI completion block):

  - secureboot.allows_mok_enrollment() — can this machine take a MOK
    enrollment at all (tri-state; guidance keys on `is True` only)
  - disks.live_media_kind() — which removal instruction to print
    ("cdrom" / "usb" / None-generic)

Both are exercised against mock efivars trees / mocked subprocess so the
verdicts are deterministic on any development host.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.backend import disks, secureboot  # noqa: E402


def _write_efivar(efivars_dir: Path, name: str, byte_value: int) -> None:
    """4-byte attribute header + 1-byte payload, per the efivars format."""
    path = efivars_dir / f"{name}-{secureboot._EFI_GLOBAL_GUID}"
    path.write_bytes(b"\x07\x00\x00\x00" + bytes([byte_value]))


class TestIsSetupMode(unittest.TestCase):
    def test_setup_mode_one_reads_true(self):
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SetupMode", 1)
            self.assertIs(secureboot.is_setup_mode(Path(td)), True)

    def test_setup_mode_zero_reads_false(self):
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SetupMode", 0)
            self.assertIs(secureboot.is_setup_mode(Path(td)), False)

    def test_absent_reads_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(secureboot.is_setup_mode(Path(td)))


class TestAllowsMokEnrollment(unittest.TestCase):
    def test_non_efi_is_false(self):
        """BIOS boot: no shim/MokManager path exists — known-incapable."""
        with tempfile.TemporaryDirectory() as td:
            missing_efi = Path(td) / "no-such-efi-tree"
            self.assertIs(
                secureboot.allows_mok_enrollment(
                    efivars_dir=Path(td), efi_dir=missing_efi),
                False)

    def test_efi_with_secureboot_var_is_true(self):
        """The SecureBoot variable reading — at EITHER value — proves the
        firmware exposes Secure Boot machinery."""
        with tempfile.TemporaryDirectory() as td:
            for value in (0, 1):
                _write_efivar(Path(td), "SecureBoot", value)
                self.assertIs(
                    secureboot.allows_mok_enrollment(
                        efivars_dir=Path(td), efi_dir=Path(td)),
                    True)

    def test_efi_with_only_setupmode_var_is_true(self):
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SetupMode", 0)
            self.assertIs(
                secureboot.allows_mok_enrollment(
                    efivars_dir=Path(td), efi_dir=Path(td)),
                True)

    def test_efi_with_neither_var_is_none(self):
        """EFI tree present but no Secure Boot machinery readable:
        capability UNKNOWN — callers must not print confident guidance."""
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(
                secureboot.allows_mok_enrollment(
                    efivars_dir=Path(td), efi_dir=Path(td)))


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr="")


class TestLiveMediaKind(unittest.TestCase):
    def test_no_live_mount_returns_none(self):
        with mock.patch.object(disks.subprocess, "run",
                               return_value=_completed(returncode=1)):
            self.assertIsNone(disks.live_media_kind())

    def test_optical_source_is_cdrom(self):
        """A VM's virtual CD/DVD drive presents as sr0 regardless of bus."""
        def fake_run(cmd, **kwargs):
            if cmd[0] == "findmnt":
                if cmd[-1] == "/run/iso":
                    return _completed(stdout="/dev/sr0\n")
                return _completed(returncode=1)
            raise AssertionError(f"unexpected command {cmd}")

        with mock.patch.object(disks.subprocess, "run", side_effect=fake_run):
            self.assertEqual(disks.live_media_kind(), "cdrom")

    def test_usb_partition_resolves_via_parent_disk(self):
        """A partition row carries no transport; the parent disk answers."""
        def fake_run(cmd, **kwargs):
            if cmd[0] == "findmnt":
                if cmd[-1] == "/run/iso":
                    return _completed(stdout="/dev/sdb1\n")
                return _completed(returncode=1)
            if cmd[0] == "lsblk" and cmd[-1] == "/dev/sdb1":
                return _completed(stdout='PKNAME="sdb" TRAN=""\n')
            if cmd[0] == "lsblk" and cmd[-1] == "/dev/sdb":
                return _completed(stdout='PKNAME="" TRAN="usb"\n')
            raise AssertionError(f"unexpected command {cmd}")

        with mock.patch.object(disks.subprocess, "run", side_effect=fake_run):
            self.assertEqual(disks.live_media_kind(), "usb")

    def test_whole_disk_usb_source(self):
        """An ISO written raw to the stick mounts the whole-disk node."""
        def fake_run(cmd, **kwargs):
            if cmd[0] == "findmnt":
                if cmd[-1] == "/run/iso":
                    return _completed(stdout="/dev/sdb\n")
                return _completed(returncode=1)
            if cmd[0] == "lsblk":
                return _completed(stdout='PKNAME="" TRAN="usb"\n')
            raise AssertionError(f"unexpected command {cmd}")

        with mock.patch.object(disks.subprocess, "run", side_effect=fake_run):
            self.assertEqual(disks.live_media_kind(), "usb")

    def test_loop_source_keeps_scanning_then_finds_medium(self):
        """The squashfs loop mount must not end classification early."""
        def fake_run(cmd, **kwargs):
            if cmd[0] == "findmnt":
                if cmd[-1] == "/run/iso":
                    return _completed(stdout="/dev/loop0\n")
                if cmd[-1] == "/run/squashfs":
                    return _completed(stdout="/dev/sr0\n")
                return _completed(returncode=1)
            raise AssertionError(f"unexpected command {cmd}")

        with mock.patch.object(disks.subprocess, "run", side_effect=fake_run):
            self.assertEqual(disks.live_media_kind(), "cdrom")

    def test_non_usb_non_optical_returns_none(self):
        """An unclassifiable medium falls back to generic wording."""
        def fake_run(cmd, **kwargs):
            if cmd[0] == "findmnt":
                if cmd[-1] == "/run/iso":
                    return _completed(stdout="/dev/vda2\n")
                return _completed(returncode=1)
            if cmd[0] == "lsblk" and cmd[-1] == "/dev/vda2":
                return _completed(stdout='PKNAME="vda" TRAN=""\n')
            if cmd[0] == "lsblk" and cmd[-1] == "/dev/vda":
                return _completed(stdout='PKNAME="" TRAN=""\n')
            raise AssertionError(f"unexpected command {cmd}")

        with mock.patch.object(disks.subprocess, "run", side_effect=fake_run):
            self.assertIsNone(disks.live_media_kind())

    def test_findmnt_timeout_fails_safe(self):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

        with mock.patch.object(disks.subprocess, "run", side_effect=fake_run):
            self.assertIsNone(disks.live_media_kind())


if __name__ == "__main__":
    unittest.main()

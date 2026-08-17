# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Unit tests for the D-2 Secure Boot-aware MOK-skip guard.

Covers the testable logic layer:
  - installer.backend.secureboot.is_secure_boot_enabled (mock efivars)
  - InstallerState.mok_skip_needs_ack matrix
  - the is_ready_for_install / validation_errors gate

Runs on any host: no efivars, no root, no display required. Uses a temp dir
as the efivars root (same approach as test_class2_runtime_sb_state.py). The
GTK Confirm-screen rendering of the warning + ack row is the display/VM
gate and is NOT exercised here.
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from installer.backend import secureboot
from installer.frontend.gui.state import InstallerState


def _write_secureboot(efivars_dir: Path, byte_value: int) -> None:
    """Write a mock SecureBoot EFI variable (4 attr bytes + 1 payload)."""
    path = efivars_dir / f"SecureBoot-{secureboot._EFI_GLOBAL_GUID}"
    # 4-byte little-endian attribute header (value irrelevant) + 1 payload byte
    path.write_bytes(struct.pack("<I", 0x6) + bytes([byte_value]))


def _ready_state(**overrides) -> InstallerState:
    """An InstallerState with all non-MOK required fields satisfied."""
    s = InstallerState()
    s.target_disk = "/dev/sda"
    s.confirm_destructive = True
    s.username = "user"
    s.user_password = "pw12345678"
    s.user_password_confirm = "pw12345678"
    s.root_password = "rootpw12345"
    s.root_password_confirm = "rootpw12345"
    s.hostname = "intergenos"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestIsSecureBootEnabled(unittest.TestCase):
    def test_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            _write_secureboot(Path(td), 1)
            self.assertIs(secureboot.is_secure_boot_enabled(Path(td)), True)

    def test_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            _write_secureboot(Path(td), 0)
            self.assertIs(secureboot.is_secure_boot_enabled(Path(td)), False)

    def test_absent_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            # No SecureBoot variable written (non-EFI / no SB).
            self.assertIsNone(secureboot.is_secure_boot_enabled(Path(td)))

    def test_truncated_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / f"SecureBoot-{secureboot._EFI_GLOBAL_GUID}"
            path.write_bytes(b"\x00\x00")  # shorter than the 4-byte header
            self.assertIsNone(secureboot.is_secure_boot_enabled(Path(td)))


class TestIsEfiFirmware(unittest.TestCase):
    def test_efi_present(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(secureboot.is_efi_firmware(Path(td)))

    def test_efi_absent(self):
        # A path that does not exist -> not EFI (BIOS host).
        self.assertFalse(
            secureboot.is_efi_firmware(Path("/nonexistent/efi/firmware/xyz"))
        )


class TestMokSkipNeedsAck(unittest.TestCase):
    def test_enforcing_and_skipped_needs_ack(self):
        s = _ready_state(secure_boot_enabled=True, mok_password="")
        self.assertTrue(s.mok_skip_needs_ack())

    def test_enforcing_but_password_set_no_ack(self):
        s = _ready_state(secure_boot_enabled=True, mok_password="mokpw12345")
        self.assertFalse(s.mok_skip_needs_ack())

    def test_sb_off_no_ack(self):
        s = _ready_state(secure_boot_enabled=False, mok_password="")
        self.assertFalse(s.mok_skip_needs_ack())

    def test_sb_unknown_no_ack(self):
        # None (non-EFI / unreadable) must NOT raise the HARD ack-gate.
        s = _ready_state(secure_boot_enabled=None, mok_password="")
        self.assertFalse(s.mok_skip_needs_ack())


class TestMokSkipSbUnknownOnEfi(unittest.TestCase):
    """D-2 hardening: distinguish non-EFI (benign) from EFI-but-unreadable."""

    def test_unknown_on_efi_soft_note(self):
        # SB state unreadable on a UEFI host -> soft informational note.
        s = _ready_state(
            secure_boot_enabled=None, firmware_is_efi=True, mok_password="",
        )
        self.assertTrue(s.mok_skip_sb_unknown_on_efi())
        # Soft note only — it does NOT hard-gate the Install button.
        self.assertFalse(s.mok_skip_needs_ack())
        self.assertTrue(s.is_ready_for_install())

    def test_unknown_on_bios_benign(self):
        # None + non-EFI (BIOS) -> genuinely benign, no note.
        s = _ready_state(
            secure_boot_enabled=None, firmware_is_efi=False, mok_password="",
        )
        self.assertFalse(s.mok_skip_sb_unknown_on_efi())

    def test_password_set_no_note(self):
        s = _ready_state(
            secure_boot_enabled=None, firmware_is_efi=True,
            mok_password="mokpw12345",
        )
        self.assertFalse(s.mok_skip_sb_unknown_on_efi())

    def test_known_enforcing_uses_hard_gate_not_soft_note(self):
        s = _ready_state(
            secure_boot_enabled=True, firmware_is_efi=True, mok_password="",
        )
        self.assertFalse(s.mok_skip_sb_unknown_on_efi())
        self.assertTrue(s.mok_skip_needs_ack())


class TestInstallGate(unittest.TestCase):
    def test_gate_blocks_skip_under_sb(self):
        # HARD BLOCK: SB on + no MOK -> install cannot proceed, full stop.
        s = _ready_state(secure_boot_enabled=True, mok_password="")
        self.assertFalse(s.is_ready_for_install())
        self.assertIn(
            "Secure Boot is ON and MOK enrollment is skipped",
            " ".join(s.validation_errors()),
        )

    def test_gate_has_no_acknowledge_escape(self):
        # The old acknowledge-and-proceed escape is GONE (decided
        # 2026-06-05). Even setting the former ack flag does not unblock — the
        # only non-bricking paths are enroll a MOK or disable Secure Boot.
        s = _ready_state(secure_boot_enabled=True, mok_password="")
        s.mok_skip_acknowledged = True  # legacy attr; must be ignored now
        self.assertFalse(s.is_ready_for_install())

    def test_gate_passes_when_password_set(self):
        # Enrolling a MOK is one of the two non-bricking paths.
        s = _ready_state(secure_boot_enabled=True, mok_password="mokpw12345")
        self.assertTrue(s.is_ready_for_install())

    def test_gate_passes_when_sb_off(self):
        # SB off -> MOK enrollment is OPTIONAL; a blank MOK does not block.
        s = _ready_state(secure_boot_enabled=False, mok_password="")
        self.assertTrue(s.is_ready_for_install())


if __name__ == "__main__":
    unittest.main()

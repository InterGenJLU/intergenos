# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The install must read its own NVRAM write back, and record what it meant.

Before this, bootloader.py wrote the UEFI boot entry, stated in the trace that
InterGenOS was first, and never read NVRAM again. A firmware that accepted the
write and then presented a different BootOrder was invisible until someone
looked at the installed machine (measured 2026-07-31: `BootOrder: 0001,0000`,
0001 being the firmware's own entry for the ESP's fallback loader).

Two things are pinned here:
  - _verify_bootorder_end_state reads NVRAM back and records the observed
    order, our bootnums, and whether that matches the install's intent — as a
    report, never as an install failure;
  - _write_boot_default_intent records the default-boot-target decision on the
    target, which is what keeps the boot-time checker from overriding a user
    who deliberately kept another operating system first.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from installer.backend import bootloader
from installer.backend.bootloader import (
    BOOTLOADER_ID,
    BOOT_DEFAULT_INTENT_REL,
    _verify_bootorder_end_state,
    _write_boot_default_intent,
)

# What the AMI board actually held after the install: our entry second, the
# firmware's own fallback-loader entry first, both on the same ESP.
_DEMOTED = (f"BootCurrent: 0001\n"
            f"BootOrder: 0001,0000\n"
            f"Boot0000* {BOOTLOADER_ID}\tHD(1,GPT,aaaa,0x800,0x200000)"
            f"/\\EFI\\InterGenOS\\shimx64.efi\n"
            f"Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)"
            f"/\\EFI\\BOOT\\BOOTX64.EFI\n")

_AS_INTENDED = (f"BootCurrent: 0000\n"
                f"BootOrder: 0000,0001\n"
                f"Boot0000* {BOOTLOADER_ID}\tHD(1,GPT,aaaa,0x800,0x200000)"
                f"/\\EFI\\InterGenOS\\shimx64.efi\n"
                f"Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)"
                f"/\\EFI\\BOOT\\BOOTX64.EFI\n")


class _TraceCapture(unittest.TestCase):
    def _capture(self, nvram=None, rc=0):
        self.events = []

        def fake_traced_run_chroot(target, cmd, **kw):
            return (rc, nvram or "", "")

        def fake_trace_event(name, **kw):
            self.events.append((name, kw))

        o1 = bootloader.trace.traced_run_chroot
        o2 = bootloader.trace.trace_event
        bootloader.trace.traced_run_chroot = fake_traced_run_chroot
        bootloader.trace.trace_event = fake_trace_event
        self.addCleanup(lambda: setattr(bootloader.trace,
                                        "traced_run_chroot", o1))
        self.addCleanup(lambda: setattr(bootloader.trace, "trace_event", o2))

    def _event(self, name):
        for got, kw in self.events:
            if got == name:
                return kw
        self.fail(f"no {name} trace event; got {[e[0] for e in self.events]}")


class VerifyEndStateTests(_TraceCapture):
    def test_firmware_demotion_is_recorded_as_a_mismatch(self):
        self._capture(nvram=_DEMOTED)
        _verify_bootorder_end_state("/t", expect_default=True)
        kw = self._event("efibootmgr_bootorder_verified")
        self.assertTrue(kw["verified"])
        self.assertEqual(kw["boot_order"], "0001,0000")
        self.assertEqual(kw["our_bootnums"], "0000")
        self.assertFalse(kw["observed_first_is_ours"])
        self.assertFalse(kw["matches_intent"])

    def test_intended_order_is_recorded_as_a_match(self):
        self._capture(nvram=_AS_INTENDED)
        _verify_bootorder_end_state("/t", expect_default=True)
        kw = self._event("efibootmgr_bootorder_verified")
        self.assertTrue(kw["observed_first_is_ours"])
        self.assertTrue(kw["matches_intent"])

    def test_declined_default_matches_when_we_are_not_first(self):
        self._capture(nvram=_DEMOTED)
        _verify_bootorder_end_state("/t", expect_default=False)
        kw = self._event("efibootmgr_bootorder_verified")
        self.assertFalse(kw["observed_first_is_ours"])
        self.assertTrue(kw["matches_intent"],
                        "not being first IS the intent when the user kept "
                        "another operating system as the default")

    def test_unreadable_nvram_is_undetermined_not_a_verdict(self):
        self._capture(nvram="", rc=2)
        _verify_bootorder_end_state("/t", expect_default=True)
        kw = self._event("efibootmgr_bootorder_verified")
        self.assertFalse(kw["verified"])
        self.assertNotIn("matches_intent", kw)

    def test_verification_never_raises(self):
        self._capture(nvram="garbage that parses to nothing\n")
        _verify_bootorder_end_state("/t", expect_default=True)  # must not raise
        self._event("efibootmgr_bootorder_verified")


class IntentFileTests(_TraceCapture):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = self._tmp.name

    def _read(self):
        return (Path(self.target) / BOOT_DEFAULT_INTENT_REL).read_text()

    def test_default_yes_is_recorded(self):
        self._capture()
        _write_boot_default_intent(self.target, expect_default=True,
                                   foreign_before=[])
        text = self._read()
        self.assertIn("default_boot_target=yes", text)
        self.assertIn(f"boot_entry_label={BOOTLOADER_ID}", text)
        self.assertIn("foreign_os_entries_at_install=0", text)
        kw = self._event("boot_default_intent_recorded")
        self.assertEqual(kw["default_boot_target"], "yes")

    def test_declined_default_is_recorded_with_the_foreign_count(self):
        self._capture()
        _write_boot_default_intent(self.target, expect_default=False,
                                   foreign_before=["0001", "0002"])
        text = self._read()
        self.assertIn("default_boot_target=no", text)
        self.assertIn("foreign_os_entries_at_install=2", text)

    def test_file_is_world_readable_for_the_checker(self):
        self._capture()
        _write_boot_default_intent(self.target, expect_default=True,
                                   foreign_before=[])
        path = Path(self.target) / BOOT_DEFAULT_INTENT_REL
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_write_failure_is_traced_and_does_not_raise(self):
        self._capture()
        blocked = Path(self.target) / "etc"
        blocked.write_text("not a directory")  # mkdir(parents) will fail
        _write_boot_default_intent(self.target, expect_default=True,
                                   foreign_before=[])
        kw = self._event("boot_default_intent_write_failed")
        self.assertIn("error", kw)


if __name__ == "__main__":
    unittest.main()

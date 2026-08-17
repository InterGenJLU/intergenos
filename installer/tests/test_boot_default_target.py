# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""D1 / work-plan 1.25 — default UEFI boot target.

efibootmgr --create unconditionally prepends the new InterGenOS entry to
BootOrder, silently making it the machine default. On multi-OS metal that
displaces the user's prior default (a user-control violation). These tests
pin: (1) the NVRAM parse + foreign-installed-OS detection that gates the ask,
(2) has_other_os_boot_entries()'s tri-state probe, and (3) _apply_default_boot_
choice()'s decision matrix — reorder only when a foreign OS existed AND the user
declined; otherwise keep the prepend.
"""
from __future__ import annotations

import unittest

from installer.backend import bootloader
from installer.backend.bootloader import (
    _parse_efibootmgr_output,
    _foreign_os_bootnums,
    _apply_default_boot_choice,
    has_other_os_boot_entries,
    BOOTLOADER_ID,
)

# A realistic multi-OS efibootmgr dump: our entry + Windows (both on-disk =
# HD(...)), plus firmware USB/network entries that must NOT count as "another OS".
_MULTI_OS = f"""BootCurrent: 0001
Timeout: 1 seconds
BootOrder: 0001,0000,2001,2002
Boot0000* {BOOTLOADER_ID}\tHD(1,GPT,aaaa,0x800,0x100000)/File(\\EFI\\InterGenOS\\shimx64.efi)
Boot0001* Windows Boot Manager\tHD(1,GPT,bbbb,0x800,0x32000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)
Boot2001* EFI USB Device\tPciRoot(0x0)/Pci(0x14,0x0)/USB(0x1,0x0)
Boot2002* EFI Network\tMAC(001122334455,0)
"""

_SINGLE_OS = f"""BootCurrent: 0000
BootOrder: 0000,2001
Boot0000* {BOOTLOADER_ID}\tHD(1,GPT,aaaa,0x800,0x100000)/File(\\EFI\\InterGenOS\\shimx64.efi)
Boot2001* EFI USB Device\tPciRoot(0x0)/Pci(0x14,0x0)/USB(0x1,0x0)
"""


class ParseAndDetectTests(unittest.TestCase):
    def test_parse_captures_order_active_and_path(self):
        order, entries = _parse_efibootmgr_output(_MULTI_OS)
        self.assertEqual(order, ["0001", "0000", "2001", "2002"])
        active, label, path = entries["0001"]
        self.assertTrue(active)
        self.assertEqual(label, "Windows Boot Manager")
        self.assertIn("HD(", path)

    def test_foreign_excludes_ours_and_firmware_noise(self):
        _order, entries = _parse_efibootmgr_output(_MULTI_OS)
        foreign = _foreign_os_bootnums(entries, BOOTLOADER_ID)
        # Windows (HD path, not ours) counts; our own entry + the USB/network
        # firmware entries (no HD path) do not.
        self.assertEqual(foreign, ["0001"])

    def test_single_os_has_no_foreign_entries(self):
        _order, entries = _parse_efibootmgr_output(_SINGLE_OS)
        self.assertEqual(_foreign_os_bootnums(entries, BOOTLOADER_ID), [])

    def test_inactive_foreign_entry_is_ignored(self):
        dump = (f"BootOrder: 0001\n"
                f"Boot0001  Some Other OS\tHD(1,GPT,cccc,0x800,0x1000)/File(\\x.efi)\n")
        _order, entries = _parse_efibootmgr_output(dump)
        self.assertFalse(entries["0001"][0])  # inactive (space, not '*')
        self.assertEqual(_foreign_os_bootnums(entries, BOOTLOADER_ID), [])


class HasOtherOsProbeTests(unittest.TestCase):
    def _patch_efibootmgr(self, rc, stdout, exc=None):
        import subprocess

        class _R:
            returncode = rc
            stdout = ""

        def fake_run(cmd, capture_output=False, text=False):
            if exc:
                raise exc
            r = _R(); r.returncode = rc; r.stdout = stdout
            return r

        self._orig = subprocess.run
        subprocess.run = fake_run
        self.addCleanup(lambda: setattr(subprocess, "run", self._orig))

    def test_true_when_foreign_os_present(self):
        self._patch_efibootmgr(0, _MULTI_OS)
        self.assertIs(has_other_os_boot_entries(), True)

    def test_false_when_single_os(self):
        self._patch_efibootmgr(0, _SINGLE_OS)
        self.assertIs(has_other_os_boot_entries(), False)

    def test_none_when_efibootmgr_fails(self):
        self._patch_efibootmgr(1, "")
        self.assertIsNone(has_other_os_boot_entries())

    def test_none_when_efibootmgr_absent(self):
        self._patch_efibootmgr(0, "", exc=FileNotFoundError())
        self.assertIsNone(has_other_os_boot_entries())


class ApplyDefaultBootChoiceTests(unittest.TestCase):
    """Drive _apply_default_boot_choice with a fake chroot efibootmgr + a trace
    capture, asserting exactly when `efibootmgr -o` (a BootOrder rewrite) is
    issued. POST-create NVRAM has InterGenOS (Boot0006) first, Windows second."""

    _POST_CREATE = (f"BootOrder: 0006,0001,2001\n"
                    f"Boot0001* Windows Boot Manager\tHD(1,GPT,bbbb,0x800,0x32000)/File(\\bootmgfw.efi)\n"
                    f"Boot0006* {BOOTLOADER_ID}\tHD(1,GPT,aaaa,0x800,0x100000)/File(\\shimx64.efi)\n"
                    f"Boot2001* EFI USB Device\tPciRoot(0x0)/USB(0x1,0x0)\n")

    def setUp(self):
        self.chroot_cmds = []
        self.events = []

        def fake_traced_run_chroot(target, cmd):
            self.chroot_cmds.append(cmd)
            if cmd == "efibootmgr":
                return (0, self._POST_CREATE, "")
            return (0, "", "")

        def fake_trace_event(name, **kw):
            self.events.append((name, kw))

        self._o1 = bootloader.trace.traced_run_chroot
        self._o2 = bootloader.trace.trace_event
        bootloader.trace.traced_run_chroot = fake_traced_run_chroot
        bootloader.trace.trace_event = fake_trace_event
        self.addCleanup(lambda: setattr(bootloader.trace, "traced_run_chroot", self._o1))
        self.addCleanup(lambda: setattr(bootloader.trace, "trace_event", self._o2))

    def _order_cmds(self):
        return [c for c in self.chroot_cmds if c.startswith("efibootmgr -o")]

    def test_declined_on_multi_os_demotes_to_last(self):
        _apply_default_boot_choice("/t", make_default_boot=False,
                                   foreign_before=["0001"])
        # InterGenOS (0006) moved to the END; the prior default (0001) stays first.
        self.assertEqual(self._order_cmds(), ["efibootmgr -o 0001,2001,0006"])
        self.assertTrue(any(e[0] == "efibootmgr_default_boot_target"
                            and "demoted" in e[1]["action"] for e in self.events))

    def test_accepted_keeps_prepend_no_reorder(self):
        _apply_default_boot_choice("/t", make_default_boot=True,
                                   foreign_before=["0001"])
        self.assertEqual(self._order_cmds(), [])
        self.assertTrue(any(e[0] == "efibootmgr_default_boot_target"
                            and "kept" in e[1]["action"] for e in self.events))

    def test_single_os_keeps_prepend_no_reorder(self):
        _apply_default_boot_choice("/t", make_default_boot=False,
                                   foreign_before=[])
        self.assertEqual(self._order_cmds(), [])
        # No efibootmgr read even needed when there are no foreign entries.
        self.assertTrue(any(e[0] == "efibootmgr_default_boot_target"
                            for e in self.events))


if __name__ == "__main__":
    unittest.main()


# The EXACT device path the ge9b-04 dogfood install captured (ASRock X870,
# 2026-07-16): the install stick's own firmware-synthesized entry carries a
# trailing HD(...) partition node, which the bare HD() test misread as an
# installed OS — Forge flagged its own boot medium (PI-ge9b04-B).
_STICK_WITH_HD_NODE = (
    "PciRoot(0x0)/Pci(0x2,0x1)/Pci(0x0,0x0)/Pci(0xc,0x0)/Pci(0x0,0x0)"
    "/USB(1,0)/USB(0,0)/HD(2,GPT,36323032-3730-4631-b032-303132303039,"
    "0x180b960,0x55000)0000424f"
)


class RemovableMediaNotForeignTests(unittest.TestCase):
    """PI-ge9b04-B: removable-media entries never count as an installed OS,
    even when their device path carries a partition (HD) node."""

    def test_usb_stick_with_hd_node_is_not_foreign(self):
        dump = (f"BootCurrent: 0001\nBootOrder: 0000,0001\n"
                f"Boot0000* {BOOTLOADER_ID}\tHD(1,GPT,aaaa,0x800,0x100000)"
                f"/File(\\EFI\\InterGenOS\\shimx64.efi)\n"
                f"Boot0001* UEFI: SanDisk, Partition 2\t{_STICK_WITH_HD_NODE}\n")
        _order, entries = _parse_efibootmgr_output(dump)
        self.assertEqual(_foreign_os_bootnums(entries, BOOTLOADER_ID), [],
                         "the install stick was flagged as a foreign OS again")

    def test_real_foreign_os_still_detected_beside_stick(self):
        dump = (f"BootCurrent: 0001\nBootOrder: 0000,0001,0002\n"
                f"Boot0000* {BOOTLOADER_ID}\tHD(1,GPT,aaaa,0x800,0x100000)"
                f"/File(\\EFI\\InterGenOS\\shimx64.efi)\n"
                f"Boot0001* UEFI: SanDisk, Partition 2\t{_STICK_WITH_HD_NODE}\n"
                f"Boot0002* Windows Boot Manager\tHD(1,GPT,bbbb,0x800,0x32000)"
                f"/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n")
        _order, entries = _parse_efibootmgr_output(dump)
        self.assertEqual(_foreign_os_bootnums(entries, BOOTLOADER_ID),
                         ["0002"],
                         "removable exclusion must not hide a REAL foreign OS")

    def test_sd_and_cdrom_nodes_also_excluded(self):
        dump = (f"BootOrder: 0001,0002\n"
                f"Boot0001* UEFI: SD Card\tPciRoot(0x0)/Pci(0x14,0x0)"
                f"/SD(0)/HD(1,MBR,0x0,0x800,0x1000)\n"
                f"Boot0002* UEFI: DVD Drive\tPciRoot(0x0)/Pci(0x17,0x0)"
                f"/Sata(0,0,0)/CDROM(1)/HD(2,GPT,dddd,0x800,0x1000)\n")
        _order, entries = _parse_efibootmgr_output(dump)
        self.assertEqual(_foreign_os_bootnums(entries, BOOTLOADER_ID), [])

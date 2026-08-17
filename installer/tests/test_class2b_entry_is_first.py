# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Class 2b: being IN BootOrder is not the same as being what boots.

The evaluation battery required the InterGenOS entry to exist and to appear
in BootOrder. Both passed on a machine whose firmware had put its own
"UEFI OS" entry for \\EFI\\BOOT\\BOOTX64.EFI first, so the registered entry
was never the one the firmware loaded. This pins the probe that closes that
gap — required when the install recorded InterGenOS as the default boot
target, reported only when it did not.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from installer.tests.class2b_boot_order import (
    _parse_efibootmgr,
    probe_entry_is_first,
    read_default_boot_intent,
)

_DEMOTED = """BootCurrent: 0001
BootOrder: 0001,0000
Boot0000* InterGenOS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\InterGenOS\\shimx64.efi
Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\BOOT\\BOOTX64.EFI
"""

_FIRST = """BootCurrent: 0000
BootOrder: 0000,0001
Boot0000* InterGenOS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\InterGenOS\\shimx64.efi
Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\BOOT\\BOOTX64.EFI
"""


def _probe(dump, expect_default):
    entries, order, _current = _parse_efibootmgr(dump)
    return probe_entry_is_first(entries, order, "InterGenOS", expect_default)


class EntryIsFirstProbeTests(unittest.TestCase):
    def test_demoted_entry_fails_when_we_are_meant_to_be_default(self):
        r = _probe(_DEMOTED, expect_default=True)
        self.assertTrue(r.required)
        self.assertFalse(r.passed)
        self.assertEqual(r.observed, "0001")
        self.assertIn("different entry", r.detail)

    def test_first_entry_passes(self):
        r = _probe(_FIRST, expect_default=True)
        self.assertTrue(r.required)
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "0000")

    def test_declined_default_is_reported_not_required(self):
        r = _probe(_DEMOTED, expect_default=False)
        self.assertFalse(r.required)
        self.assertTrue(r.passed)
        self.assertIn("another operating system", r.detail)

    def test_no_recorded_intent_asserts_nothing(self):
        r = _probe(_DEMOTED, expect_default=None)
        self.assertFalse(r.required)
        self.assertTrue(r.passed)
        self.assertIn("no recorded install intent", r.detail)

    def test_empty_boot_order_fails_when_default_expected(self):
        dump = ("Boot0000* InterGenOS\tHD(1,GPT,aaaa,0x800,0x200000)"
                "/\\EFI\\InterGenOS\\shimx64.efi\n")
        r = _probe(dump, expect_default=True)
        self.assertTrue(r.required)
        self.assertFalse(r.passed)


class IntentReaderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "boot-default.conf"

    def test_yes_reads_true(self):
        self.path.write_text("# comment\ndefault_boot_target=yes\n")
        self.assertIs(read_default_boot_intent(str(self.path)), True)

    def test_no_reads_false(self):
        self.path.write_text("default_boot_target=no\nboot_entry_label=X\n")
        self.assertIs(read_default_boot_intent(str(self.path)), False)

    def test_absent_file_reads_none(self):
        self.assertIsNone(read_default_boot_intent(str(self.path)))

    def test_unparsable_value_reads_none(self):
        self.path.write_text("default_boot_target=maybe\n")
        self.assertIsNone(read_default_boot_intent(str(self.path)))


if __name__ == "__main__":
    unittest.main()

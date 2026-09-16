# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""R001.3 gating row 32 — the three files under /EFI/BOOT are a deliberate
signed set, and none of them may be dropped quietly.

The row described "two grub-install byproducts on the EFI partition" and
asked whether they should be removed. Measured 2026-09-16 on this
R001.2-03 install, read-only: there are THREE files under /EFI/BOOT and
no others, each byte-identical to its counterpart under /EFI/InterGenOS
by hash and by comparison, and each carries a real signature — the shim
two, from the Microsoft UEFI authorities of 2011 and 2023; GRUB one, from
this project's machine owner key; MokManager one, from the Fedora Secure
Boot authority. Capture 33-efi-fallback-set-measured.log, sha256 opening
2d15d4fe9db4d79 (the evidence set carries the whole digest). They are the
removable-media fallback the installer stages on purpose for firmware
that only looks at /EFI/BOOT/bootx64.efi. Nothing is removed; the row's
wording is what was wrong.

What still needed closing is the other direction. Nothing in the tree
held the SET together, and its MokManager member has already cost a real
bootloop: shim looks for mmx64.efi only in the directory it was launched
from, so a fallback boot with an enrolment pending and no MokManager
beside it hard-fails. This file holds all three members and the
destination names.
"""

import unittest
from unittest.mock import patch

from installer.backend import bootloader


class TestTheFallbackSetIsComplete(unittest.TestCase):

    def test_all_three_members_are_declared(self):
        sources = [src for src, _ in bootloader.EFI_FALLBACK_COPIES]
        self.assertEqual(
            sorted(sources),
            sorted([bootloader.SHIM_BINARY, bootloader.GRUB_BINARY,
                    bootloader.MOKMANAGER_BINARY]))

    def test_shim_is_mirrored_under_the_name_firmware_looks_for(self):
        by_source = dict(bootloader.EFI_FALLBACK_COPIES)
        self.assertEqual(by_source[bootloader.SHIM_BINARY], "bootx64.efi")

    def test_mok_manager_keeps_the_name_shim_searches_for(self):
        """The member whose absence bootlooped a machine.

        shim searches its own launch directory for this exact name; a
        rename or an omission reproduces that failure.
        """
        by_source = dict(bootloader.EFI_FALLBACK_COPIES)
        self.assertEqual(by_source[bootloader.MOKMANAGER_BINARY], "mmx64.efi")

    def test_grub_keeps_its_name_so_shim_finds_it_alongside(self):
        by_source = dict(bootloader.EFI_FALLBACK_COPIES)
        self.assertEqual(by_source[bootloader.GRUB_BINARY], "grubx64.efi")


class TestTheStagingCommandCopiesEveryMember(unittest.TestCase):

    def test_every_declared_pair_is_issued_as_a_copy(self):
        with patch.object(bootloader.trace, "traced_run_chroot",
                          return_value=(0, "", "")) as run:
            staged = bootloader.stage_efi_fallback_copies("/mnt/target")

        self.assertEqual(staged, bootloader.EFI_FALLBACK_COPIES)
        command = run.call_args.args[1]
        self.assertIn(f"mkdir -p {bootloader.EFI_FALLBACK_DIR}", command)
        for src, dst in bootloader.EFI_FALLBACK_COPIES:
            self.assertIn(
                f"cp {bootloader.ESP_BOOT_DIR}/{src} "
                f"{bootloader.EFI_FALLBACK_DIR}/{dst}",
                command)

    def test_a_failed_copy_is_raised_rather_than_returned_as_success(self):
        with patch.object(bootloader.trace, "traced_run_chroot",
                          return_value=(1, "", "no space left on device")):
            with self.assertRaises(RuntimeError) as caught:
                bootloader.stage_efi_fallback_copies("/mnt/target")
        self.assertIn("no space left on device", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

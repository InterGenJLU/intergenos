# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An encrypted install's crypttab carries no discard permission.

Decided 2026-09-19: an encrypted root is opened without permission to pass
discards down to the drive, so the pattern of which blocks the filesystem is
using is not published to the hardware. The word was present in the generated
file and had no effect: the initramfs that opens the volume
(installer/init/fde-init.sh) reads only the "tpm2" and "fido2" tokens out of
the options field and passes no discard permission to cryptsetup, so a machine
installed from this tree ran with the word in /etc/crypttab and a mapping that
refused discards. Measured on an installed encrypted machine: the file read
"luks,discard,x-initrd.attach" while `dmsetup table cryptroot` carried no
allow_discards flag and the mapper reported a discard granularity of 0.

A written option that nothing honours is worse than no option, because the
person reading their own disk configuration draws the wrong conclusion from it.
These tests pin the word out of all three places that could put it back: the
two layouts disks.py returns, and the writer's own default list.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.backend import config, disks
from installer.backend.config import generate_crypttab

FORBIDDEN = "discard"


def _options(target) -> list[str]:
    path = Path(target) / "etc" / "crypttab"
    for line in path.read_text().splitlines():
        if line.startswith("cryptroot"):
            return line.split()[3].split(",")
    raise AssertionError("no cryptroot line in the generated crypttab")


class TheWriterDefaultCarriesNoDiscard(unittest.TestCase):
    """config.generate_crypttab's own default list, used when a caller passes
    no crypt_opts at all — the third site, and the one a reader of disks.py
    alone would never see."""

    def setUp(self):
        self._orig = config._get_uuid
        config._get_uuid = lambda dev: "2ccf1b34-38aa-4f7f-b85d-7b1df989ae87"
        self.addCleanup(lambda: setattr(config, "_get_uuid", self._orig))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = self._tmp.name

    def test_no_crypt_opts_key_means_no_discard(self):
        generate_crypttab(self.target,
                          {"luks_enabled": True, "root": "/dev/nvme0n1p2"})
        self.assertNotIn(FORBIDDEN, _options(self.target))

    def test_the_options_still_open_with_luks(self):
        generate_crypttab(self.target,
                          {"luks_enabled": True, "root": "/dev/nvme0n1p2"})
        self.assertEqual(_options(self.target)[0], "luks",
                         "the type token must stay first; fde-init.sh and "
                         "systemd-cryptsetup both read this field")

    def test_an_explicit_list_is_written_as_given(self):
        """The writer must not add the word back for a caller that did not
        ask for it — and must not strip a caller's list either, since the
        experimental unlock tokens ride in the same field."""
        generate_crypttab(self.target, {
            "luks_enabled": True, "root": "/dev/nvme0n1p2",
            "crypt_opts": ["luks", "tpm2", "fido2"],
        })
        opts = _options(self.target)
        self.assertNotIn(FORBIDDEN, opts)
        self.assertIn("tpm2", opts)
        self.assertIn("fido2", opts)


class _PartitionDiskCase(unittest.TestCase):
    """Drives the real _partition_disk body with every destructive primitive
    replaced, so the layout dictionary under test is the one the installer
    actually builds rather than a copy of it."""

    def _layout(self, efi: bool) -> dict:
        with patch.object(disks, "_run"), \
             patch.object(disks, "_release_disk"), \
             patch.object(disks, "cryptsetup_available", return_value=True), \
             patch.object(disks, "luks2_format"), \
             patch.object(disks, "luks_open",
                          return_value="/dev/mapper/cryptroot"), \
             patch.object(disks, "_partition_paths",
                          return_value=("/dev/nvme0n1p1", "/dev/nvme0n1p2")):
            return disks._partition_disk(
                "/dev/nvme0n1",
                disks.FRESH_INSTALL_MIN_DISK_BYTES + 1,
                efi=efi,
                luks_enabled=True,
                luks_passphrase="a passphrase",
                tpm2_enabled=False,
                fido2_enabled=False,
                fido2_progress_callback=None,
            )


class TheLayoutsCarryNoDiscard(_PartitionDiskCase):
    def test_the_efi_encrypted_layout(self):
        layout = self._layout(efi=True)
        self.assertTrue(layout["luks_enabled"])
        self.assertNotIn(FORBIDDEN, layout["crypt_opts"])

    def test_the_bios_encrypted_layout(self):
        layout = self._layout(efi=False)
        self.assertTrue(layout["luks_enabled"])
        self.assertNotIn(FORBIDDEN, layout["crypt_opts"])

    def test_the_layouts_still_name_the_luks_type(self):
        for efi in (True, False):
            with self.subTest(efi=efi):
                self.assertEqual(self._layout(efi=efi)["crypt_opts"][0], "luks")


class TheWordIsGoneEndToEnd(_PartitionDiskCase):
    """The layout disks.py returns is handed straight to the writer by
    install.py, so the two halves are proven joined rather than separately."""

    def setUp(self):
        self._orig = config._get_uuid
        config._get_uuid = lambda dev: "2ccf1b34-38aa-4f7f-b85d-7b1df989ae87"
        self.addCleanup(lambda: setattr(config, "_get_uuid", self._orig))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = self._tmp.name

    def test_partitioner_to_crypttab(self):
        generate_crypttab(self.target, self._layout(efi=True))
        text = (Path(self.target) / "etc" / "crypttab").read_text()
        self.assertNotIn(FORBIDDEN, text,
                         "the word must not reach the written file by any "
                         "path, including a comment line")


if __name__ == "__main__":
    unittest.main()

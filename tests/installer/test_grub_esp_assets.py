# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""GRUB assets/config must resolve on the unencrypted ESP, not the root fs.

On a full-disk-encryption install the root fs is LUKS/argon2id, which GRUB
cannot unlock — so the installed grub's menu config, theme, and font must live
on the ESP, and /etc/default/grub must point GRUB_THEME/GRUB_BACKGROUND at
ESP-relative paths (resolved against $root, which the core image sets to the
ESP). These guard against a regression back to /boot/grub paths that would leave
FDE installs unable to read their own grub config.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from installer.backend import config  # noqa: E402


class TestGrubEspAssetPaths(unittest.TestCase):
    def _gen(self, partitions):
        with tempfile.TemporaryDirectory() as td:
            config.generate_grub_defaults(td, partitions)
            return (Path(td) / "etc" / "default" / "grub").read_text()

    def test_theme_and_background_resolve_on_esp(self):
        # Non-existent devices → blkid fails → cmdline falls back gracefully;
        # we only assert the GRUB_THEME/GRUB_BACKGROUND asset paths here.
        grub_def = self._gen({"root": "/dev/does-not-exist", "efi": True})
        self.assertIn(
            'GRUB_THEME="/EFI/InterGenOS/themes/intergenos/theme.txt"', grub_def
        )
        self.assertIn(
            'GRUB_BACKGROUND="/EFI/InterGenOS/themes/intergenos/backgrounds/'
            'grub_background.png"',
            grub_def,
        )

    def test_no_root_fs_grub_asset_paths_on_efi(self):
        # A /boot/grub theme/background path is unreadable on an FDE install.
        grub_def = self._gen({"root": "/dev/does-not-exist", "efi": True})
        self.assertNotIn('GRUB_THEME="/boot/grub', grub_def)
        self.assertNotIn('GRUB_BACKGROUND="/boot/grub', grub_def)

    def test_bios_keeps_root_fs_paths(self):
        # BIOS has no ESP and GRUB reads the root fs — keep the legacy paths so
        # the EFI ESP-relocation does not regress the BIOS menu theme.
        grub_def = self._gen({"root": "/dev/does-not-exist", "efi": False})
        self.assertIn(
            'GRUB_THEME="/boot/grub/themes/intergenos/theme.txt"', grub_def
        )
        self.assertIn('GRUB_BACKGROUND="/boot/grub/grub_background.png"', grub_def)
        self.assertNotIn("/EFI/InterGenOS/themes", grub_def)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The installer's embedded GRUB_SBAT_CSV must stay byte-identical to the
canonical packages/core/grub/sbat.csv.

The ISO grub embeds packages/core/grub/sbat.csv via `grub-mkstandalone --sbat`
(scripts/build-grub-standalone.sh); the installer embeds GRUB_SBAT_CSV into the
installed-system grub via `grub-mkimage --sbat` (bootloader._grub_mkimage_sbat_efi).
If the two drift, installed-grub and ISO-grub would carry different SBAT
generations — a Secure Boot trust-chain inconsistency. The embedded duplicate
is allowed only because this byte-equality gate makes drift un-mergeable.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from installer.backend import bootloader  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_CSV = REPO_ROOT / "packages" / "core" / "grub" / "sbat.csv"


class TestGrubSbatCsvMatchesCanonical(unittest.TestCase):
    def test_byte_identical_to_canonical(self):
        canonical = CANONICAL_CSV.read_text()
        self.assertEqual(
            bootloader.GRUB_SBAT_CSV,
            canonical,
            "installer GRUB_SBAT_CSV drifted from packages/core/grub/sbat.csv; "
            "they MUST be byte-identical so installed-grub and ISO-grub embed "
            "the same SBAT generations.",
        )

    def test_carries_the_grub_generation(self):
        # Defensive: the grub generation line must be present (shim checks it).
        self.assertIn("\ngrub,5,", "\n" + bootloader.GRUB_SBAT_CSV)


if __name__ == "__main__":
    unittest.main()

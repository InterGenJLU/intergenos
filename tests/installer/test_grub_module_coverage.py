# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Each GRUB image must embed every module its boot config loads.

There are two GRUB images in the product, built by two independent module
lists, each reading a different config at boot:

  - the installed system's grub (installer/backend/bootloader.py,
    GRUB_EFI_MODULES, built with grub-mkimage) reads the
    grub-mkconfig-GENERATED grub.cfg on the ESP;
  - the ISO's standalone grub (scripts/build-grub-standalone.sh, MODULES
    array) reads the in-tree installer/iso/grub/grub.cfg, and also serves
    boots that read a generated config (the surface the 2026-07-30 fix
    covered by baking efi_uga/gettext/gzio/bli).

Under Secure Boot the built-in shim_lock verifier refuses any module loaded
as a loose file from the ESP, so a module that a config insmods but the
image does not embed prints "prohibited by secure boot policy" on the boot
console — a boot-time-only, easy-to-miss defect. It shipped twice because a
fix applied to one list did not reach the other. This test pins each image
against its config surface so the lists cannot silently drift again.

GENERATED_CFG_INSMODS provenance: extracted 2026-08-18 from the generated
grub.cfg of a Secure-Boot install on dual-GPU hardware
(`grep -o 'insmod [a-z_0-9]*'`, deduplicated), minus two documented classes:
  - disk-access modules grub-install auto-embeds when it probes the boot
    path (cryptodisk, luks2, gcry_* on encrypted installs) — added by
    grub-install itself, not by our lists;
  - the load_video fallback branch (ieee1275_fb, vbe, vga, video_bochs,
    video_cirrus, efi_gop, efi_uga): with all_video embedded, the generated
    load_video function takes the all_video branch only, and grub-mkimage
    embeds all_video's platform dependencies alongside it.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from installer.backend import bootloader  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ISO_BUILDER = REPO_ROOT / "scripts" / "build-grub-standalone.sh"
ISO_GRUB_CFG = REPO_ROOT / "installer" / "iso" / "grub" / "grub.cfg"

# Modules the grub-mkconfig-generated grub.cfg insmods on an installed system.
GENERATED_CFG_INSMODS = {
    "all_video",
    "bli",
    "chain",
    "ext2",
    "fat",
    "gettext",
    "gfxterm",
    "gzio",
    "part_gpt",
    "png",
    "regexp",
}

# The generated-config subset the ISO standalone image bakes explicitly for
# boots where it reads a generated config (efi_uga: its load_video fallback
# branch — the standalone image does not embed all_video).
STANDALONE_GENERATED_SUBSET = {"efi_uga", "gettext", "gzio", "bli", "regexp"}

# Embedding all_video embeds its platform video backends as dependencies.
ALL_VIDEO_DEPS = {"efi_gop", "efi_uga", "video_bochs", "video_cirrus"}


def iso_module_set():
    text = ISO_BUILDER.read_text()
    match = re.search(r"^MODULES=\((.*?)^\)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError("MODULES array not found in build-grub-standalone.sh")
    body = re.sub(r"#[^\n]*", "", match.group(1))
    return set(body.split())


def installer_module_set():
    return set(bootloader.GRUB_EFI_MODULES.split())


def iso_cfg_insmods():
    return set(re.findall(r"^\s*insmod\s+([a-z_0-9]+)", ISO_GRUB_CFG.read_text(), re.MULTILINE))


class TestGrubModuleCoverage(unittest.TestCase):
    def test_installed_grub_embeds_generated_cfg_insmods(self):
        installer = installer_module_set()
        covered = installer | (ALL_VIDEO_DEPS if "all_video" in installer else set())
        missing = GENERATED_CFG_INSMODS - covered
        self.assertFalse(
            missing,
            "Installed-system grub does not embed modules the generated "
            "grub.cfg insmods (Secure Boot refuses loose ESP module loads): "
            f"{sorted(missing)}",
        )

    def test_iso_grub_embeds_its_own_cfg_insmods(self):
        iso = iso_module_set()
        covered = iso | (ALL_VIDEO_DEPS if "all_video" in iso else set())
        missing = iso_cfg_insmods() - covered
        self.assertFalse(
            missing,
            "ISO standalone grub does not embed modules its own grub.cfg "
            f"insmods: {sorted(missing)}",
        )

    def test_iso_grub_keeps_generated_cfg_subset(self):
        iso = iso_module_set()
        missing = STANDALONE_GENERATED_SUBSET - iso
        self.assertFalse(
            missing,
            "ISO standalone grub dropped generated-config modules it bakes "
            f"for generated-config boots: {sorted(missing)}",
        )

    def test_parsers_sane(self):
        # Guard the parsers: a regex that silently matches nothing would turn
        # the coverage tests into vacuous passes.
        self.assertGreater(len(iso_module_set()), 20)
        self.assertIn("linux", iso_module_set())
        self.assertIn("gfxterm", iso_cfg_insmods())


if __name__ == "__main__":
    unittest.main()

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

import hashlib
import importlib.util
import io
import re
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from installer.backend import bootloader  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ISO_BUILDER = REPO_ROOT / "scripts" / "build-grub-standalone.sh"
ISO_GRUB_CFG = REPO_ROOT / "installer" / "iso" / "grub" / "grub.cfg"
MEMDISK_CHECKER = REPO_ROOT / "scripts" / "check-grub-memdisk-font.py"

# The memdisk path both surfaces must use. grub_font_load() resolves a bare
# font name by trying "(memdisk)/fonts/<name>.pf2" before
# "$prefix/fonts/<name>.pf2" (grub-core/font/font.c:452-467), so this exact
# path is what makes a `loadfont unicode` in the grub-mkconfig-generated config
# resolve inside the image instead of opening the ESP copy.
MEMDISK_FONT_MEMBER = "fonts/unicode.pf2"

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


def iso_cfg_loadfonts():
    """Every font path the ISO menu config loads, in file order.

    The trailing `;` of `if loadfont <path>; then` is not part of the path.
    """
    found = re.findall(r"^\s*(?:if\s+)?loadfont\s+(\S+)",
                       ISO_GRUB_CFG.read_text(), re.MULTILINE)
    return [path.rstrip(";") for path in found]


def iso_builder_memdisk_files():
    """The source-file arguments build-grub-standalone.sh hands grub-mkstandalone.

    Each is `<path-inside-memdisk>=<path-on-build-host>`; the shell variables
    are resolved from their assignments in the same script so the pin follows
    the value, not the spelling.
    """
    text = ISO_BUILDER.read_text()
    values = dict(re.findall(r'^([A-Z_0-9]+)="\$\{\1:-([^}]*)\}"', text,
                             re.MULTILINE))
    values.update(dict(re.findall(r'^([A-Z_0-9]+)="([^"$]*)"$', text,
                                  re.MULTILINE)))

    args = re.findall(r'^\s+"(\S+?)=(\S+?)"\s*\\?$', text, re.MULTILINE)
    resolved = []
    for dest, src in args:
        for name, value in values.items():
            dest = dest.replace(f"${name}", value).replace(f"${{{name}}}", value)
            src = src.replace(f"${name}", value).replace(f"${{{name}}}", value)
        resolved.append((dest, src))
    return resolved


def load_memdisk_checker():
    """Import scripts/check-grub-memdisk-font.py as a module."""
    spec = importlib.util.spec_from_file_location("check_grub_memdisk_font",
                                                  MEMDISK_CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_memdisk_tar(member, payload):
    """A ustar archive holding one member — the shape both builders embed."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        info.mtime = 0
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


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


class TestGrubFontLoadsFromInsideTheImage(unittest.TestCase):
    """Neither GRUB image may load its console font off the ESP.

    GRUB opens fonts as GRUB_FILE_TYPE_FONT (grub-core/font/font.c:434,454) and
    the built-in shim_lock verifier has no skip-list entry for that type
    (grub-core/kern/efi/sb.c), so under Secure Boot every ESP font read is
    refused with "prohibited by secure boot policy" and the menu falls back to
    grub's built-in font. Reads from the embedded memdisk are exempt
    (grub-core/kern/verifiers.c) because the image carrying those bytes is
    itself signature-verified.

    Like the module-coverage class above, this is a boot-time-only defect: both
    builders succeed and both artifacts boot either way. These pins hold the two
    independent builders to the memdisk path so one of them cannot drift back.
    """

    def _fire_mkimage(self, image_factory):
        """Run _grub_mkimage_sbat_efi against a temp target with a stub chroot.

        image_factory receives the memdisk archive the code just wrote and
        returns the bytes the stubbed grub-mkimage "produces", so the real
        verifier runs against a real image shape.
        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp,
                                                            ignore_errors=True))
        font_bytes = b"PFF2-not-a-real-font-just-bytes" * 64
        font = tmp / "host-unicode.pf2"
        font.write_bytes(font_bytes)

        commands = []
        tar_rel = bootloader.GRUB_MEMDISK_TAR_CHROOT.lstrip("/")

        def fake_chroot(target, cmd, **kwargs):
            commands.append(cmd)
            if "grub-mkimage " in cmd:
                out = (Path(target) / bootloader.ESP_BOOT_DIR.lstrip("/")
                       / bootloader.GRUB_BINARY)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(image_factory((Path(target) / tar_rel).read_bytes()))
            return (0, "", "")

        blkid = mock.Mock(returncode=0, stdout="1234-ABCD\n")
        with mock.patch.object(bootloader.trace, "traced_run_chroot",
                               fake_chroot), \
             mock.patch.object(bootloader.subprocess, "run",
                               return_value=blkid), \
             mock.patch.object(bootloader, "HOST_GRUB_UNICODE_PF2", font):
            bootloader._grub_mkimage_sbat_efi(str(tmp), {"esp": "/dev/sda1"})

        return commands, tmp, font_bytes

    @staticmethod
    def _good_image(tar_bytes):
        return b"MZ" + b"\0" * 64 + tar_bytes + b"/EFI/InterGenOS\0"

    def _mkimage_command(self, commands):
        """The grub-mkimage invocation alone.

        The traced command string starts with a `mkdir -p …` — searching the
        whole string for " -p " would find that instead of grub-mkimage's own
        prefix flag and read the argument order backwards.
        """
        found = [c for c in commands if "grub-mkimage " in c]
        self.assertEqual(len(found), 1,
                         f"expected exactly one grub-mkimage call, got {found}")
        return found[0][found[0].index("grub-mkimage "):]

    # --- installed-system surface (installer/backend/bootloader.py) ---

    def test_installed_image_embeds_memdisk_and_tar_modules(self):
        modules = installer_module_set()
        missing = {"memdisk", "tar"} - modules
        self.assertFalse(
            missing,
            "The core image carries the font in a memdisk; 'memdisk' provides "
            "the device and 'tar' reads files out of it. grub-mkimage pushes "
            f"neither for you. Missing: {sorted(missing)}",
        )

    def test_installed_mkimage_passes_memdisk_before_prefix(self):
        commands, _tmp, _font = self._fire_mkimage(self._good_image)
        cmd = self._mkimage_command(commands)
        self.assertIn(" -m ", cmd)
        self.assertIn(" -p ", cmd)
        self.assertLess(
            cmd.index(" -m "), cmd.index(" -p "),
            "grub-mkimage assigns prefix='(memdisk)/boot/grub' whenever it "
            "handles -m, discarding an earlier -p (util/grub-mkimage.c:189-198). "
            "-m must come first or the image loses its ESP prefix and cannot "
            f"find grub.cfg or its modules. Command: {cmd}",
        )

    def test_installed_early_config_loads_font_from_the_memdisk(self):
        _commands, tmp, _font = self._fire_mkimage(self._good_image)
        load_cfg = (tmp / "tmp/igos-grub-load.cfg").read_text()
        self.assertIn(f"loadfont (memdisk)/{MEMDISK_FONT_MEMBER}", load_cfg)
        self.assertNotIn("loadfont /EFI/", load_cfg)

    def test_installed_memdisk_archive_carries_the_font_bytes(self):
        _commands, tmp, font_bytes = self._fire_mkimage(self._good_image)
        archive = (tmp / bootloader.GRUB_MEMDISK_TAR_CHROOT.lstrip("/")).read_bytes()
        member = bootloader.read_memdisk_member(archive, MEMDISK_FONT_MEMBER)
        self.assertIsNotNone(member,
                             "the memdisk archive has no font member")
        self.assertEqual(hashlib.sha256(member).hexdigest(),
                         hashlib.sha256(font_bytes).hexdigest())

    def test_installed_build_refuses_an_image_missing_the_font(self):
        with self.assertRaises(RuntimeError) as caught:
            self._fire_mkimage(lambda _tar: b"MZ" + b"\0" * 4096)
        self.assertIn("memdisk", str(caught.exception))

    def test_installed_build_refuses_a_clobbered_prefix(self):
        def clobbered(tar_bytes):
            return self._good_image(tar_bytes) + b"(memdisk)/boot/grub\0"

        with self.assertRaises(RuntimeError) as caught:
            self._fire_mkimage(clobbered)
        self.assertIn("prefix", str(caught.exception))

    def test_installed_font_member_is_the_memdisk_first_path(self):
        # A bare `loadfont unicode` — what the grub-mkconfig-generated config
        # emits — is resolved against "(memdisk)/fonts/<name>.pf2" before the
        # prefix. Any other member path leaves the generated config reading the
        # ESP copy, which is the refusal this whole change removes.
        self.assertEqual(bootloader.GRUB_MEMDISK_FONT_MEMBER,
                         MEMDISK_FONT_MEMBER)

    # --- live-ISO surface (scripts/build-grub-standalone.sh + its cfg) ---

    def test_iso_builder_bakes_the_font_into_the_memdisk(self):
        files = iso_builder_memdisk_files()
        self.assertTrue(files, "no memdisk source-file arguments parsed")
        dests = {dest for dest, _src in files}
        self.assertIn(
            MEMDISK_FONT_MEMBER, dests,
            "build-grub-standalone.sh must hand grub-mkstandalone the console "
            f"font at {MEMDISK_FONT_MEMBER}; got {sorted(dests)}",
        )
        src = dict(files)[MEMDISK_FONT_MEMBER]
        self.assertTrue(src.endswith("unicode.pf2"),
                        f"font source is not a .pf2 path: {src}")

    def test_iso_cfg_loads_every_font_from_the_memdisk(self):
        loads = iso_cfg_loadfonts()
        self.assertTrue(loads, "no loadfont found in the ISO grub.cfg")
        for path in loads:
            self.assertEqual(
                path, f"(memdisk)/{MEMDISK_FONT_MEMBER}",
                "the ISO menu config must load its font from inside the signed "
                f"image; {path} is read through the verifier and refused under "
                "Secure Boot",
            )

    # --- the reader both builders' checks depend on ---

    def test_memdisk_readers_agree(self):
        # bootloader.py ships inside the forge package and cannot import the
        # build script, so the reader exists twice. This is the anti-drift pin.
        checker = load_memdisk_checker()
        payload = b"font-bytes" * 137
        image = (b"MZ" + b"\0" * 32
                 + make_memdisk_tar(MEMDISK_FONT_MEMBER, payload))
        self.assertEqual(
            bootloader.read_memdisk_member(image, MEMDISK_FONT_MEMBER), payload)
        self.assertEqual(
            checker.read_memdisk_member(image, MEMDISK_FONT_MEMBER), payload)

    def test_memdisk_readers_reject_a_bare_name_occurrence(self):
        # The same path appears as plain text in the embedded config, which is
        # in the image too. A reader that accepted any occurrence would report a
        # font the image does not carry — a check that passes for the wrong
        # reason.
        checker = load_memdisk_checker()
        image = b"loadfont (memdisk)/" + MEMDISK_FONT_MEMBER.encode() + b"\n" * 600
        self.assertIsNone(
            bootloader.read_memdisk_member(image, MEMDISK_FONT_MEMBER))
        self.assertIsNone(
            checker.read_memdisk_member(image, MEMDISK_FONT_MEMBER))

    def test_memdisk_readers_skip_a_decoy_and_return_the_real_member(self):
        # The test above is satisfied by the size field failing to parse, so on
        # its own it does not pin the ustar-magic check. This one does: the
        # decoy carries a parseable size at the header offset and only lacks the
        # magic, and it sits BEFORE the real member, so a reader that skips the
        # magic check returns the decoy's bytes instead of the font's.
        checker = load_memdisk_checker()
        payload = b"real-font-bytes" * 91

        decoy = bytearray(512 + 100)
        name = MEMDISK_FONT_MEMBER.encode()
        decoy[0:len(name)] = name
        decoy[124:136] = b"00000000144\0"        # octal 144 = 100 bytes
        decoy[257:262] = b"nomag"                # everything but the magic
        decoy[512:612] = b"D" * 100

        image = bytes(decoy) + make_memdisk_tar(MEMDISK_FONT_MEMBER, payload)
        for reader in (bootloader.read_memdisk_member,
                       checker.read_memdisk_member):
            self.assertEqual(
                reader(image, MEMDISK_FONT_MEMBER), payload,
                "the reader returned the decoy block instead of the real "
                "memdisk member",
            )

    def test_font_parsers_sane(self):
        # Same non-vacuity guard as the module parsers: each of these regexes
        # would turn its test into a vacuous pass if it matched nothing.
        self.assertGreaterEqual(len(iso_cfg_loadfonts()), 1)
        self.assertGreaterEqual(len(iso_builder_memdisk_files()), 2)
        self.assertIn("boot/grub/grub.cfg",
                      {dest for dest, _src in iso_builder_memdisk_files()})
        self.assertTrue(MEMDISK_CHECKER.is_file())


if __name__ == "__main__":
    unittest.main()

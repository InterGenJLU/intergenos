# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The installed system's boot menu is composed after its boot image exists.

Measured on a real R001.3 install on 2026-10-16 (encrypted root, Secure Boot
on): the package-phase fire of the kernel's hook refused to sign, the
bootloader phase ran grub-mkconfig while /boot/efi/EFI/Linux held no image
(install trace row 7289: the menu's only entry was the firmware's), and the
encrypted-install re-run of the hook built and signed the image a step later
(row 7296). The menu the installer wrote had no InterGenOS entry, and the
machine had nothing of its own to boot. A plain install had no re-run at all,
so the same refusal left it with no image.

This runs the REAL installer orchestration (run_install) and the REAL
bootloader module over a scratch target. What is stood in, and only that: the
commands the bootloader phase runs inside the target. The stand-in for
grub-mkconfig composes the menu from the images on the ESP at the moment it
runs, as /etc/grub.d/06_uki does; the stand-in for the kernel's hook writes a
signed image, as the real hook does with the passphrase; the stand-in for
sbverify accepts exactly what that hook stand-in signed. The package phase is
stood in by its measured outcome: no image on the ESP.

The menu is then read by this test's own reader, not the installer's, so the
check does not lean on the code it checks.

On the tree before the change both cases ended with a menu that chainloads no
InterGenOS image.
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from installer.backend import bootloader
from installer.backend.install import run_install

KVER = "6.18.51-igos-6"
PASSPHRASE = "install-time-passphrase-1"

CFG = {
    "version": 1,
    "locale": "en_US.UTF-8",
    "timezone": "UTC",
    "hostname": "intergenos",
    "package_groups": ["core", "base"],
}


def pinned_entry_image(menu_text):
    """The image the menu's pinned default entry (--id intergenos-uki) chainloads."""
    lines = menu_text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"^\s*menuentry\b.*\s--id\s+intergenos-uki(\s|\{|$)", line):
            for body in lines[i + 1:]:
                if body.strip() == "}":
                    break
                m = re.match(r"^\s*chainloader\s+(\S+)\s*$", body)
                if m:
                    return m.group(1)
    return None


class FakeTarget:
    """The commands the bootloader phase runs in the target, stood in."""

    def __init__(self, root, signs=True, menu_lists_images=True):
        self.root = Path(root)
        self.esp_linux = self.root / "boot/efi/EFI/Linux"
        self.calls = []
        # What the stand-in generator wrote, by output path: the menu the
        # installer leaves must be exactly this, never edited afterwards.
        self.generated = {}
        # Fault switches for the two checks the installer must make itself:
        # an image that does not verify, and a menu without the image.
        self.signs = signs
        self.menu_lists_images = menu_lists_images

    def run_chroot(self, target, cmd, *args, **kwargs):
        env = kwargs.get("env") or {}
        self.calls.append(cmd)
        m = re.search(r"grub-mkconfig -o (\S+)", cmd)
        if m:
            out = self.root / m.group(1).lstrip("/")
            out.parent.mkdir(parents=True, exist_ok=True)
            ukis = sorted(self.esp_linux.glob("intergenos-*.efi"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not self.menu_lists_images:
                ukis = []
            text = ["set default=\"intergenos-uki\"", "terminal_output gfxterm",
                    "### BEGIN /etc/grub.d/06_uki ###"]
            for n, uki in enumerate(ukis):
                kv = uki.name[len("intergenos-"):-len(".efi")]
                ident = "intergenos-uki" if n == 0 else f"intergenos-uki-{kv}"
                text += [f'menuentry "InterGenOS {kv} (UKI)" --id {ident} {{',
                         "    insmod chain",
                         f"    chainloader /EFI/Linux/{uki.name}",
                         "}"]
            text += ["### END /etc/grub.d/06_uki ###",
                     "menuentry 'UEFI Firmware Settings' $menuentry_id_option 'uefi-firmware' {",
                     "    fwsetup", "}"]
            out.write_text("\n".join(text) + "\n")
            self.generated[m.group(1)] = "\n".join(text) + "\n"
            return 0, "", ""
        if "/var/lib/pkm/hooks/linux-kernel/post-install" in cmd:
            if not env.get("IGOS_MOK_PASSPHRASE"):
                return 77, "", "REFUSING TO SIGN: no usable passphrase"
            self.esp_linux.mkdir(parents=True, exist_ok=True)
            (self.esp_linux / f"intergenos-{KVER}.efi").write_bytes(
                b"SIGNED-BY-THE-MACHINE-KEY\n" if self.signs else b"UNSIGNED\n")
            return 0, "", ""
        m = re.match(r"sbverify --cert \S+ (\S+)$", cmd)
        if m:
            image = self.root / m.group(1).lstrip("/")
            ok = image.is_file() and image.read_bytes().startswith(b"SIGNED")
            return (0, "Signature verification OK", "") if ok else (
                1, "", "Signature verification failed")
        if cmd.startswith("objcopy -O binary --only-section=.initrd"):
            return 0, str(4 << 20) + "\n", ""
        return 0, "", ""


class _Base(unittest.TestCase):
    luks = False
    signs = True
    menu_lists_images = True
    # What the post-install hooks phase does to the menu the bootloader phase
    # composed and checked: None leaves it alone; "regenerate" re-runs the
    # generator over the ESP as it is; "drop-the-image" regenerates it the way a
    # generator that cannot see /EFI/Linux would.
    hooks_on_menu = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.target = self.tmp / "target"
        (self.target / "boot/efi/EFI/Linux").mkdir(parents=True)
        (self.target / f"boot/vmlinuz-{KVER}").write_bytes(b"kernel\n")
        (self.target / "boot/initramfs.img").write_bytes(b"\0" * 50)
        hook = self.target / "var/lib/pkm/hooks/linux-kernel/post-install"
        hook.parent.mkdir(parents=True)
        hook.write_text("#!/bin/bash\n")
        font = self.tmp / "unicode.pf2"
        font.write_bytes(b"font\n")
        yaml_path = self.tmp / "install.yaml"
        yaml_path.write_text(yaml.safe_dump(CFG))
        (self.tmp / "archives").mkdir()
        (self.tmp / "packages").mkdir()
        self.fake = FakeTarget(self.target, signs=self.signs,
                               menu_lists_images=self.menu_lists_images)

        partitions = {"esp": "/dev/sda1", "root": "/dev/sda2", "efi": True,
                      "luks_enabled": self.luks}
        install_io = {"disk": "/dev/sda", "username": "user",
                      "user_password": "pw1234", "root_password": "root1234",
                      "mok_key_passphrase": PASSPHRASE}
        if self.luks:
            install_io.update({"luks_enabled": True,
                               "luks_passphrase": "disk-passphrase-1"})

        patches = [
            patch("installer.backend.install.disks"),
            patch("installer.backend.install.packages"),
            patch("installer.backend.install.config"),
            patch("installer.backend.install.users"),
            patch("installer.backend.install.mok"),
            patch("installer.backend.install.hooks"),
            patch("installer.backend.install.shutil"),
            patch.object(bootloader, "verify_shim_assets_present"),
            patch.object(bootloader, "stage_uki_prereqs"),
            patch.object(bootloader, "ingest_kernel_hook_log"),
            patch.object(bootloader, "_install_signed_efi_chain"),
            patch.object(bootloader, "_stage_grub_assets_to_esp"),
            patch.object(bootloader, "HOST_GRUB_UNICODE_PF2", font),
            patch.object(bootloader.trace, "traced_run_chroot",
                         self.fake.run_chroot),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        disks, packages, _config, _users, mok, hooks, shutil_mock = mocks[:7]
        if self.hooks_on_menu is not None:
            def _hook_writes_the_menu(*_args, **_kwargs):
                if self.hooks_on_menu == "drop-the-image":
                    self.fake.menu_lists_images = False
                self.fake.run_chroot(
                    str(self.target),
                    "grub-mkconfig -o /boot/efi/EFI/InterGenOS/grub.cfg")
            hooks.run_post_install_hooks.side_effect = _hook_writes_the_menu
        shutil_mock.which.return_value = "/usr/bin/fake-binary"
        disks.is_efi.return_value = True
        disks.partition_disk.return_value = partitions
        disks.unmount_target.return_value = []
        # The package phase's measured outcome: installed, and no image on the
        # ESP, because its fire of the kernel's hook refused to sign.
        packages.install_packages.return_value = (5, 0, [], [])
        packages.compute_install_set_gap.return_value = []
        mok.generate_mok_keypair.return_value = {
            "key_path": "/var/lib/intergen/mok/mok.key",
            "cert_path": "/var/lib/intergen/mok/mok.crt",
            "der_path": "/var/lib/intergen/mok/mok.der",
        }
        self.result = run_install(str(yaml_path), install_io,
                                  str(self.tmp / "archives"),
                                  str(self.tmp / "packages"),
                                  target=str(self.target))

    def menu(self):
        return (self.target / "boot/efi/EFI/InterGenOS/grub.cfg").read_text()


class PlainInstallMenuBootsTheImage(_Base):
    luks = False

    def test_the_install_finishes(self):
        self.assertTrue(self.result.success, self.result.error_message)

    def test_the_menu_chainloads_the_installed_image(self):
        self.assertEqual(pinned_entry_image(self.menu()),
                         f"/EFI/Linux/intergenos-{KVER}.efi",
                         f"the installed menu boots no InterGenOS image:\n{self.menu()}")


class EncryptedInstallMenuBootsTheImage(_Base):
    luks = True

    def test_the_install_finishes(self):
        self.assertTrue(self.result.success, self.result.error_message)

    def test_the_menu_chainloads_the_installed_image(self):
        self.assertEqual(pinned_entry_image(self.menu()),
                         f"/EFI/Linux/intergenos-{KVER}.efi",
                         f"the installed menu boots no InterGenOS image:\n{self.menu()}")

    def test_the_image_was_built_before_the_menu(self):
        self._assert_image_before_menu()

    def _assert_image_before_menu(self):
        hook = [i for i, c in enumerate(self.fake.calls)
                if "linux-kernel/post-install" in c]
        menu = [i for i, c in enumerate(self.fake.calls) if "grub-mkconfig" in c]
        self.assertTrue(hook and menu, self.fake.calls)
        self.assertLess(hook[-1], menu[-1],
                        "the menu was composed before the last image build")


class AnImageThatDoesNotVerifyStopsTheInstall(_Base):
    """The installer reads the signature itself instead of trusting the hook."""
    signs = False

    def test_the_install_does_not_finish(self):
        self.assertFalse(self.result.success,
                         "an install whose boot image does not verify finished")
        self.assertIn("does not verify", self.result.error_message or "")


class AMenuWithoutTheImageStopsTheInstall(_Base):
    """The installer reads the composed menu instead of trusting grub-mkconfig."""
    menu_lists_images = False

    def test_the_install_does_not_finish(self):
        self.assertFalse(self.result.success,
                         "an install whose menu boots no InterGenOS image finished")
        self.assertIn("intergenos-uki", self.result.error_message or "")


class AHookThatRewritesTheMenuStopsTheInstall(_Base):
    """The post-install hooks run after the menu was checked, inside the target,
    and one that regenerates it can drop the entry; the check is repeated after
    them."""
    hooks_on_menu = "drop-the-image"

    def test_the_install_does_not_finish(self):
        self.assertFalse(self.result.success,
                         "an install whose post-install hooks left a menu that "
                         "boots no InterGenOS image finished")
        self.assertIn("after the post-install hooks ran",
                      self.result.error_message or "")


class AHookThatRegeneratesTheMenuFaithfullyIsAccepted(_Base):
    """The repeated check reads the menu, not whether it was written again: a
    regeneration that keeps the entry finishes."""
    hooks_on_menu = "regenerate"
    luks = True

    def test_the_install_finishes(self):
        self.assertTrue(self.result.success, self.result.error_message)
        self.assertEqual(pinned_entry_image(self.menu()),
                         f"/EFI/Linux/intergenos-{KVER}.efi")


class TheInstalledMenuIsTheGeneratorsOutput(_Base):
    """The installer leaves grub-mkconfig's menu exactly as generated.

    Until 2026-10-17 it appended a theme directive to the generated file, which
    the first later grub-mkconfig on the installed system dropped; the
    directive now comes from the theme package's /etc/grub.d script, so the
    installed menu and any regeneration of it are the same generator's output.
    """
    luks = True

    def test_the_menu_on_disk_is_what_the_generator_wrote(self):
        self.assertTrue(self.result.success, self.result.error_message)
        generated = self.fake.generated.get("/boot/efi/EFI/InterGenOS/grub.cfg")
        self.assertIsNotNone(generated, "the generator never wrote the menu")
        self.assertEqual(self.menu(), generated,
                         "the installer edited the menu after grub-mkconfig wrote it")

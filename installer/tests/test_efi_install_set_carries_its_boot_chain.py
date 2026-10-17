# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An EFI install set its boot chain cannot be built from is refused before
the disk is touched.

Every EFI install builds, signs and checks its boot image and composes its
boot menu after the disk has been partitioned and written
(bootloader.build_final_uki, then compose_boot_menu), from ukify
(systemd-pass2, desktop tier), sbsign and sbverify (sbsigntool, core tier) and
/etc/grub.d/06_uki (intergenos-grub-theme, desktop tier), and refuses there
without them. The text installer lets the desktop group be deselected (it
forces only core), and no check before the disk write refused the result: the
install failed at its bootloader phase with the disk already written.

These cases resolve REAL install sets: real gzip archives with a real
.PKGINFO, and a recipe tree read by the same resolver the package phase
installs from. The last case runs the real run_install with only the disk
module, the live-media binary lookup and the display probe stood in, and
proves the refusal comes before the partition phase.
"""

import io
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from installer.backend import install, packages

# (tier, name): a core package, the three the boot chain is built from, and a
# desktop package with no part in it.
MEDIA = (
    ("core", "bash"),
    ("core", "sbsigntool"),
    ("desktop", "systemd-pass2"),
    ("desktop", "intergenos-grub-theme"),
    ("desktop", "gnome-shell"),
)


def _recipe(package_dir: Path, tier, name, version="1.0", release=1):
    d = package_dir / tier / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.yml").write_text(
        f'name: {name}\nversion: "{version}"\nrelease: {release}\n'
        f"tier: {tier}\n")


def _archive(archive_dir: Path, name, version="1.0", release=1):
    """A real gzip archive whose .PKGINFO is its last member, as the builder
    writes it."""
    path = archive_dir / f"{name}-{version}-{release}.igos.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        payload = b"payload\n"
        member = tarfile.TarInfo(f"./usr/bin/{name}")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
        body = f"pkgname={name}\npkgver={version}\npkgrel={release}\n".encode()
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))


class _Media(unittest.TestCase):
    #: Names whose archive the media lacks (the recipe is still in the tree).
    omit = ()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.archives = self.tmp / "archives"
        self.package_dir = self.tmp / "packages"
        self.archives.mkdir()
        self.package_dir.mkdir()
        for tier, name in MEDIA:
            _recipe(self.package_dir, tier, name)
            if name not in self.omit:
                _archive(self.archives, name)

    def gate(self, groups, efi=True):
        install.preflight_check_efi_boot_chain(
            {"package_groups": groups}, str(self.archives),
            str(self.package_dir), efi, detected_vendors=set())


class TheResolverIsTheOneThePackagePhaseUses(_Media):
    """The fixture resolves as the package phase would: the desktop group is
    what carries two of the three."""

    def test_core_alone_resolves_without_the_desktop_two(self):
        names = {n for n, _v, _p in packages.get_group_packages(
            ["core"], str(self.archives), str(self.package_dir), set())}
        self.assertEqual(names, {"bash", "sbsigntool"})


class AnEfiSetWithoutTheDesktopGroupIsRefused(_Media):

    def test_refused_naming_what_is_missing_and_the_group(self):
        with self.assertRaises(RuntimeError) as cm:
            self.gate(["core", "base"])
        msg = str(cm.exception)
        self.assertIn("systemd-pass2 (ukify", msg)
        self.assertIn("intergenos-grub-theme (/etc/grub.d/06_uki", msg)
        self.assertNotIn("sbsigntool (", msg, "core carries the signer")
        self.assertIn("'desktop-gnome'", msg)
        self.assertIn("before any disk write", msg)


class AnEfiSetWithTheRequiredGroupsPasses(_Media):

    def test_passes(self):
        self.gate(["core", "desktop-gnome"])


class ABiosInstallIsNotHeldToIt(_Media):

    def test_passes_without_the_desktop_group(self):
        self.gate(["core"], efi=False)


class MediaWithoutTheSignerIsRefused(_Media):
    """Every group selected, and the media lacks one of the three."""
    omit = ("sbsigntool",)

    def test_refused_naming_the_signer(self):
        with self.assertRaises(RuntimeError) as cm:
            self.gate(["core", "desktop-gnome"])
        msg = str(cm.exception)
        self.assertIn("sbsigntool (sbsign and sbverify", msg)
        self.assertNotIn("systemd-pass2 (", msg)
        self.assertNotIn("not selected", msg)


class TheInstallStopsBeforeTheDiskIsTouched(_Media):
    """The real orchestration, whose every step past validate is stood in by a
    tripwire: a tree without the refusal stops at the partition step's
    tripwire, so this case can never reach a real mount or install on the
    machine running it."""

    def test_run_install_refuses_in_its_validate_phase(self):
        yaml_path = self.tmp / "install.yaml"
        yaml_path.write_text(yaml.safe_dump({
            "version": 1, "locale": "en_US.UTF-8", "timezone": "UTC",
            "hostname": "intergenos", "package_groups": ["core"]}))
        install_io = {"disk": "/dev/sdz", "username": "user",
                      "user_password": "pw1234", "root_password": "root1234",
                      "mok_key_passphrase": "install-time-passphrase-1"}
        tripwire = AssertionError("the install went past its validate phase")
        with patch("installer.backend.install.disks") as disks, \
                patch("installer.backend.install.hooks") as hooks, \
                patch("installer.backend.install.config"), \
                patch("installer.backend.install.users"), \
                patch("installer.backend.install.mok"), \
                patch("installer.backend.install.bootloader"), \
                patch("installer.backend.install.shutil") as shutil_mock, \
                patch.object(packages, "install_packages",
                             side_effect=tripwire), \
                patch.object(packages, "detect_display_pci_vendors",
                             return_value=set()):
            disks.is_efi.return_value = True
            disks.partition_disk.side_effect = tripwire
            disks.unmount_target.return_value = []
            hooks.mount_virtual_fs.side_effect = tripwire
            shutil_mock.which.return_value = "/usr/bin/fake-binary"
            result = install.run_install(
                str(yaml_path), install_io, str(self.archives),
                str(self.package_dir), target=str(self.tmp / "target"))
        self.assertFalse(result.success,
                         "an EFI install without its boot chain went on")
        self.assertIn("systemd-pass2", result.error_message or "")
        disks.partition_disk.assert_not_called()
        self.assertNotEqual(result.phase_completed, install.PHASE_VALIDATE)


if __name__ == "__main__":
    unittest.main()

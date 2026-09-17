# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""When a kernel was installed but not signed, the advisory says so.

The reboot advisory tells a person that a just-installed package needs a reboot to
take effect. That sentence becomes false, and dangerous, when the kernel hook
refused to sign the new kernel: rebooting then runs the PREVIOUS kernel, and a
person who was told "reboot to activate" has no way to know that.

The state is read rather than recorded. A marker file written by the hook would go
stale — removed by hand, left behind after a later successful signing, or absent
because the hook died before writing it. What is asked instead is the question that
actually decides the next boot: is there a signed boot image for this kernel on the
EFI system partition?
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pkm import services


def make_boot(tmp_path, kernels, ukis):
    boot = tmp_path / "boot"
    esp = boot / "efi" / "EFI" / "Linux"
    boot.mkdir(parents=True, exist_ok=True)
    esp.mkdir(parents=True, exist_ok=True)
    for kver in kernels:
        (boot / f"vmlinuz-{kver}").write_bytes(b"kernel")
    for kver in ukis:
        (esp / f"intergenos-{kver}.efi").write_bytes(b"image")
    return boot


class TestReadingWhetherTheNewKernelCanBoot:

    def test_a_kernel_with_its_own_boot_image_is_bootable(self, tmp_path):
        boot = make_boot(tmp_path, ["6.18.51-igos-2"], ["6.18.51-igos-2"])
        assert services.kernels_without_a_boot_image(boot) == []

    def test_a_kernel_with_no_boot_image_is_named(self, tmp_path):
        boot = make_boot(tmp_path, ["6.18.51-igos-2", "6.18.51-igos-1"],
                         ["6.18.51-igos-1"])
        assert services.kernels_without_a_boot_image(boot) == ["6.18.51-igos-2"]

    def test_a_machine_with_no_esp_at_all_reports_nothing(self, tmp_path):
        """A BIOS machine boots its kernel directly; there is nothing to say."""
        boot = tmp_path / "boot"
        boot.mkdir(parents=True)
        (boot / "vmlinuz-6.18.51-igos-2").write_bytes(b"kernel")
        assert services.kernels_without_a_boot_image(boot) == []

    def test_an_unreadable_boot_directory_reports_nothing_rather_than_guessing(
            self, tmp_path):
        assert services.kernels_without_a_boot_image(tmp_path / "absent") == []


class TestWhatThePersonIsTold:

    def test_the_ordinary_banner_is_unchanged_when_everything_is_signed(
            self, tmp_path):
        boot = make_boot(tmp_path, ["6.18.51-igos-2"], ["6.18.51-igos-2"])
        banner = services.format_reboot_banner(["linux-kernel"], boot_dir=boot)
        assert "REBOOT REQUIRED" in banner
        assert "NOT BOOTABLE" not in banner

    def test_an_unsigned_kernel_is_named_and_the_reboot_promise_is_withdrawn(
            self, tmp_path):
        boot = make_boot(tmp_path, ["6.18.51-igos-2"], [])
        banner = services.format_reboot_banner(["linux-kernel"], boot_dir=boot)
        assert "NOT BOOTABLE UNTIL SIGNED" in banner
        assert "6.18.51-igos-2" in banner
        assert "pkm reinstall linux-kernel" in banner

    def test_it_says_which_kernel_the_machine_will_actually_run(self, tmp_path):
        boot = make_boot(tmp_path, ["6.18.51-igos-2", "6.18.51-igos-1"],
                         ["6.18.51-igos-1"])
        banner = services.format_reboot_banner(["linux-kernel"], boot_dir=boot)
        assert "previous" in banner.lower()

    def test_nothing_installed_means_no_banner_even_with_an_unsigned_kernel(
            self, tmp_path):
        """The advisory is about this transaction, not a standing audit."""
        boot = make_boot(tmp_path, ["6.18.51-igos-2"], [])
        assert services.format_reboot_banner([], boot_dir=boot) == ""

    def test_a_non_kernel_transaction_does_not_gain_the_warning(self, tmp_path):
        boot = make_boot(tmp_path, ["6.18.51-igos-2"], [])
        banner = services.format_reboot_banner(["nvidia"], boot_dir=boot)
        assert "NOT BOOTABLE" not in banner

"""Bootloader installation for InterGenOS installer."""

import subprocess
from pathlib import Path

from .hooks import mount_virtual_fs, unmount_virtual_fs, run_chroot


def install_grub(target, disk, partitions):
    """Install GRUB bootloader to the target system.

    Args:
        target: target root path
        disk: disk device (e.g., /dev/sda)
        partitions: dict from disks.partition_disk()
    """
    target = str(target)
    efi = partitions.get("efi", False)

    mount_virtual_fs(target)

    try:
        if efi:
            # EFI mode — install to ESP
            esp_mount = f"{target}/boot/efi"
            Path(esp_mount).mkdir(parents=True, exist_ok=True)

            # Mount ESP if not already mounted
            if partitions.get("esp"):
                subprocess.run(
                    f"mountpoint -q {esp_mount} || mount {partitions['esp']} {esp_mount}",
                    shell=True, capture_output=True
                )

            rc, stdout, stderr = run_chroot(target,
                "grub-install --target=x86_64-efi "
                "--efi-directory=/boot/efi "
                "--bootloader-id=InterGenOS"
            )
            if rc != 0:
                raise RuntimeError(f"grub-install (EFI) failed: {stderr}")
        else:
            # BIOS mode — install to MBR/GPT
            rc, stdout, stderr = run_chroot(target,
                f"grub-install --target=i386-pc {disk}"
            )
            if rc != 0:
                raise RuntimeError(f"grub-install (BIOS) failed: {stderr}")

        # Generate GRUB config
        rc, stdout, stderr = run_chroot(target,
            "grub-mkconfig -o /boot/grub/grub.cfg"
        )
        if rc != 0:
            raise RuntimeError(f"grub-mkconfig failed: {stderr}")

    finally:
        unmount_virtual_fs(target)

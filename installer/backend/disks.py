"""Disk detection and partitioning for InterGenOS installer."""

import json
import os
import subprocess
from dataclasses import dataclass, field


@dataclass
class Disk:
    """Represents a block device."""
    path: str           # e.g., /dev/sda
    name: str           # e.g., sda
    size_bytes: int
    size_human: str      # e.g., "500G"
    model: str
    removable: bool
    partitions: list = field(default_factory=list)


@dataclass
class Partition:
    """Represents a partition on a disk."""
    path: str           # e.g., /dev/sda1
    number: int
    size_bytes: int
    fstype: str
    mountpoint: str
    label: str
    uuid: str


def detect_disks():
    """Detect available block devices.

    Returns list of Disk objects, excluding loop devices, RAM disks,
    and the installation media itself.
    """
    result = subprocess.run(
        ["lsblk", "-J", "-b", "-o",
         "NAME,SIZE,MODEL,RM,TYPE,FSTYPE,MOUNTPOINT,LABEL,UUID,PATH"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return []

    data = json.loads(result.stdout)
    disks = []

    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        # Skip loop, ram, sr (CD/DVD)
        name = dev.get("name", "")
        if name.startswith(("loop", "ram", "sr", "zram")):
            continue

        size = int(dev.get("size", 0))
        disk = Disk(
            path=dev.get("path", f"/dev/{name}"),
            name=name,
            size_bytes=size,
            size_human=_human_size(size),
            model=(dev.get("model") or "Unknown").strip(),
            removable=bool(dev.get("rm")),
        )

        # Collect partitions
        for child in dev.get("children", []):
            if child.get("type") == "part":
                part_size = int(child.get("size", 0))
                disk.partitions.append(Partition(
                    path=child.get("path", f"/dev/{child['name']}"),
                    number=int(child["name"].replace(name, "").lstrip("p")),
                    size_bytes=part_size,
                    fstype=child.get("fstype") or "",
                    mountpoint=child.get("mountpoint") or "",
                    label=child.get("label") or "",
                    uuid=child.get("uuid") or "",
                ))

        disks.append(disk)

    return disks


def partition_disk(disk_path, efi=False):
    """Partition a disk for InterGenOS installation.

    Creates a GPT partition table with:
    - EFI mode: ESP (512MB FAT32) + root (rest, ext4)
    - BIOS mode: bios_grub (1MB) + root (rest, ext4)

    Returns dict with partition paths.
    """
    # Wipe existing partition table
    subprocess.run(["wipefs", "-a", disk_path], check=True,
                   capture_output=True)

    if efi:
        # GPT + EFI System Partition + root
        _run(f"parted -s {disk_path} mklabel gpt")
        _run(f"parted -s {disk_path} mkpart ESP fat32 1MiB 513MiB")
        _run(f"parted -s {disk_path} set 1 esp on")
        _run(f"parted -s {disk_path} mkpart root ext4 513MiB 100%")

        # Determine partition paths (handles nvme vs sda naming)
        p1, p2 = _partition_paths(disk_path, 2)

        # Format
        _run(f"mkfs.fat -F32 {p1}")
        _run(f"mkfs.ext4 -L intergenos {p2}")

        return {"esp": p1, "root": p2, "efi": True}
    else:
        # GPT + BIOS boot + root
        _run(f"parted -s {disk_path} mklabel gpt")
        _run(f"parted -s {disk_path} mkpart bios_grub 1MiB 2MiB")
        _run(f"parted -s {disk_path} set 1 bios_grub on")
        _run(f"parted -s {disk_path} mkpart root ext4 2MiB 100%")

        p1, p2 = _partition_paths(disk_path, 2)

        _run(f"mkfs.ext4 -L intergenos {p2}")

        return {"bios_grub": p1, "root": p2, "efi": False}


def mount_target(partitions, target="/mnt/target"):
    """Mount partitions for installation."""
    os.makedirs(target, exist_ok=True)

    # Mount root
    _run(f"mount {partitions['root']} {target}")

    # Mount ESP if EFI
    if partitions.get("efi"):
        esp_mount = f"{target}/boot/efi"
        os.makedirs(esp_mount, exist_ok=True)
        _run(f"mount {partitions['esp']} {esp_mount}")

    return target


def unmount_target(target="/mnt/target"):
    """Unmount all filesystems under target."""
    # Unmount in reverse order
    for sub in ["boot/efi", "dev/pts", "dev", "proc", "sys", "run"]:
        path = f"{target}/{sub}"
        subprocess.run(["umount", path], capture_output=True)
    subprocess.run(["umount", target], capture_output=True)


def is_efi():
    """Check if the system booted in EFI mode."""
    return os.path.isdir("/sys/firmware/efi")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _run(cmd):
    """Run a shell command, raise on failure."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result


def _partition_paths(disk_path, count):
    """Get partition device paths (handles /dev/sda1 vs /dev/nvme0n1p1)."""
    # NVMe drives use 'p' separator
    sep = "p" if "nvme" in disk_path or "mmcblk" in disk_path else ""
    return tuple(f"{disk_path}{sep}{i}" for i in range(1, count + 1))


def _human_size(bytes_val):
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"

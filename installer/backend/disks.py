"""Disk detection and partitioning for InterGenOS installer.

Supports two install modes:
- 'fresh': wipe the entire disk and create a clean GPT layout.
- 'alongside': shrink an existing NTFS partition (Windows) and install
  InterGenOS into the freed space, preserving Windows + its EFI partition
  for dual-boot via shared ESP.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum


class InstallMode(Enum):
    FRESH = "fresh"          # wipe disk, clean slate
    ALONGSIDE = "alongside"  # shrink existing OS partition, share ESP


# Minimum free space we require for an InterGenOS root partition (250 GB)
# Per Q6 partition math (signing_key_custody discussion).
ALONGSIDE_MIN_ROOT_BYTES = 250 * 1024**3


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
        is_root_disk = False
        for child in dev.get("children", []):
            if child.get("type") == "part":
                part_size = int(child.get("size", 0))
                mountpoint = child.get("mountpoint") or ""
                disk.partitions.append(Partition(
                    path=child.get("path", f"/dev/{child['name']}"),
                    number=int(child["name"].replace(name, "").lstrip("p")),
                    size_bytes=part_size,
                    fstype=child.get("fstype") or "",
                    mountpoint=mountpoint,
                    label=child.get("label") or "",
                    uuid=child.get("uuid") or "",
                ))
                # Exclude disks containing the running root filesystem
                if mountpoint == "/":
                    is_root_disk = True

        if is_root_disk:
            continue

        disks.append(disk)

    return disks


def partition_disk(disk_path, efi=False):
    """Partition a disk for InterGenOS installation.

    Creates a GPT partition table with:
    - EFI mode: ESP (512MB FAT32) + root (rest, ext4)
    - BIOS mode: bios_grub (1MB) + root (rest, ext4)

    Returns dict with partition paths.
    """
    # Wipe existing partition table (routed through _run so dry_run is honored)
    _run(["wipefs", "-a", disk_path])

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


def detect_existing_esp(disk):
    """Find an existing EFI System Partition on a disk (Windows install).

    Returns the Partition object if found, else None. Used to share an ESP
    in alongside-install mode rather than carving a new one.
    """
    for p in disk.partitions:
        if p.fstype == "vfat" and 100 * 1024**2 <= p.size_bytes <= 2 * 1024**3:
            # ESP is FAT32, typically 100MB - 2GB. Don't trust label alone.
            return p
    return None


def is_bitlocker_encrypted(partition_path):
    """Check if an NTFS partition is BitLocker-encrypted.

    BitLocker-encrypted volumes are reported by blkid with TYPE="BitLocker"
    rather than the usual ntfs/exfat. We must NEVER attempt to shrink a
    BitLocker volume — ntfsresize would either refuse or, worse, corrupt
    the encrypted data. Return True if encrypted (skip this partition).
    """
    result = subprocess.run(
        ["blkid", "-s", "TYPE", "-o", "value", partition_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    return "bitlocker" in result.stdout.strip().lower()


def detect_shrinkable_ntfs(disk, min_free_bytes=ALONGSIDE_MIN_ROOT_BYTES):
    """Find an NTFS partition that can be shrunk to make room.

    Returns (partition, free_bytes_after_shrink) tuple if a suitable NTFS
    partition is found, else (None, 0). The partition must:
    - have NTFS filesystem (not BitLocker — encrypted volumes are skipped)
    - have at least `min_free_bytes` of free space inside it (not just total
      size — we can only shrink to the size of actual used data plus a
      safety margin)

    NOTE: This function only IDENTIFIES; it does not shrink. Shrinking is
    a separate destructive step requiring explicit user confirmation.

    BitLocker-encrypted partitions are SILENTLY EXCLUDED here. The TUI
    should call detect_bitlocker_partitions() separately to surface these
    to the user with an informative message ("disable BitLocker in Windows
    first, or choose Fresh install").
    """
    candidates = []
    for p in disk.partitions:
        if p.fstype != "ntfs":
            continue
        # Skip BitLocker-encrypted volumes — never attempt to shrink these
        if is_bitlocker_encrypted(p.path):
            continue
        # Probe used space via ntfsresize --info --no-action
        result = subprocess.run(
            ["ntfsresize", "--info", "--no-action", p.path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            continue
        # Parse "You might resize at NNNNNNNNNN bytes or X.X MB" line
        used_bytes = _parse_ntfsresize_info(result.stdout)
        if used_bytes is None:
            continue
        free_after_shrink = p.size_bytes - used_bytes
        if free_after_shrink >= min_free_bytes:
            candidates.append((p, free_after_shrink))

    if not candidates:
        return None, 0

    # Pick the partition with the most free space
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


def detect_bitlocker_partitions(disk):
    """List BitLocker-encrypted partitions on a disk.

    Used by the TUI to inform the user when alongside-install isn't
    available because their Windows volume is encrypted. Returns a list
    of Partition objects.
    """
    return [p for p in disk.partitions
            if p.fstype in ("ntfs", "BitLocker") and is_bitlocker_encrypted(p.path)]


def shrink_ntfs(partition_path, new_size_bytes):
    """Shrink an NTFS partition to free space for InterGenOS.

    Uses ntfsresize to shrink the filesystem, then parted to shrink the
    partition to match. CALLER must have already taken a snapshot or
    backup — this is destructive and irreversible without restore.

    Args:
        partition_path: e.g., /dev/nvme0n1p4
        new_size_bytes: new total partition size in bytes (must be larger
                       than current NTFS used space + 1GB safety margin)

    Raises:
        RuntimeError if ntfsresize or parted fails.
    """
    # Step 1: shrink the NTFS filesystem itself
    new_size_mib = new_size_bytes // (1024 * 1024)
    _run(f"ntfsresize --force --size {new_size_mib}M {partition_path}")

    # Step 2: shrink the partition to match (using parted resizepart)
    disk_path, part_num = _split_partition_path(partition_path)
    new_size_mb = new_size_bytes // (1000 * 1000)  # parted uses MB (decimal)
    _run(f"parted -s {disk_path} resizepart {part_num} {new_size_mb}MB")


def partition_disk_alongside(disk, ntfs_partition, free_start_mb):
    """Create InterGenOS partitions in space freed by ntfs shrink.

    Args:
        disk: Disk object (the parent disk)
        ntfs_partition: the now-shrunken NTFS partition (its end_offset is
                        where our free space begins)
        free_start_mb: byte offset where free space starts (in MiB),
                      typically the end of the shrunken NTFS

    Returns dict like partition_disk(): {"esp": <existing_esp_path>,
                                          "root": <new_root_path>,
                                          "efi": True,
                                          "alongside": True}
    """
    # Alongside mode requires EFI (Windows installs are always EFI on
    # modern hardware). Also share the existing ESP rather than carving
    # a new one.
    existing_esp = detect_existing_esp(disk)
    if not existing_esp:
        raise RuntimeError(
            "Alongside install requires an existing EFI System Partition "
            "(Windows ESP). None found."
        )

    # Create the InterGenOS root partition in the free space
    _run(f"parted -s {disk.path} mkpart root ext4 {free_start_mb}MiB 100%")

    # Kernel partition table re-read + udev settle before re-detecting.
    # Without these, detect_disks() can race against the new partition's
    # /dev/ node appearing and either miss the partition ("Disk disappeared")
    # or pick a stale partition number. Both are catastrophic since we'd
    # then format the wrong partition.
    _run(f"partprobe {disk.path}")
    _run("udevadm settle")

    # Find the new partition's path (it'll be the highest-numbered partition)
    # Re-detect to get the new partition number after parted creates it
    new_disk = next((d for d in detect_disks() if d.path == disk.path), None)
    if not new_disk:
        raise RuntimeError(f"Disk {disk.path} disappeared after partitioning")
    new_root = max(new_disk.partitions, key=lambda p: p.number)

    # Format the new root
    _run(f"mkfs.ext4 -L intergenos {new_root.path}")

    return {
        "esp": existing_esp.path,
        "root": new_root.path,
        "efi": True,
        "alongside": True,
        "shared_esp": True,
    }


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

_DRY_RUN = False


def set_dry_run(enabled: bool):
    """Enable or disable dry-run mode globally."""
    global _DRY_RUN
    _DRY_RUN = enabled


def _run(cmd):
    """Run a command as a list (no shell), raise on failure.

    In dry-run mode, logs the command without executing it.
    Accepts either a list ["parted", "-s", "/dev/sda", ...] or a string
    that will be split with shlex. List form is preferred for safety —
    no shell metacharacter interpretation.
    """
    import shlex
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
    else:
        cmd_list = cmd

    if _DRY_RUN:
        print(f"  [DRY-RUN] {' '.join(cmd_list)}")
        import types
        result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return result
    result = subprocess.run(cmd_list, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd_list)}\n{result.stderr}")
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


def _parse_ntfsresize_info(output):
    """Parse used-bytes from `ntfsresize --info` output.

    Looks for lines like:
        You might resize at 123456789012 bytes or 123456 MB
    Returns the bytes value as int, or None if not found.
    """
    import re
    m = re.search(r"resize at (\d+) bytes", output)
    if m:
        return int(m.group(1))
    return None


def _split_partition_path(part_path):
    """Split /dev/sda1 -> ('/dev/sda', 1), /dev/nvme0n1p3 -> ('/dev/nvme0n1', 3)."""
    if "nvme" in part_path or "mmcblk" in part_path:
        idx = part_path.rfind("p")
        return part_path[:idx], int(part_path[idx + 1:])
    i = len(part_path)
    while i > 0 and part_path[i - 1].isdigit():
        i -= 1
    return part_path[:i], int(part_path[i:])

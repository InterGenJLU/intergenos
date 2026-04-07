# Code Review Request: InterGenOS System Installer (Forge)

I'm requesting a thorough code review of the system installer for InterGenOS, a Linux distribution built entirely from source following Linux From Scratch 13.0.

Forge is a custom system installer with a Python backend and ncurses TUI frontend. It guides the user through a 6-screen installation flow: welcome/hardware detection, disk selection and partitioning, system configuration (hostname, timezone, locale), package group selection, installation progress, and completion.

The backend handles GPT disk partitioning (with both EFI and legacy BIOS support), deploys packages to the target filesystem using pkm (our package manager) with `--root`, generates system configuration files (fstab from UUIDs, hostname, network, branding), installs the GRUB bootloader, orchestrates post-install hooks inside a chroot of the target, and creates user accounts with sudo access.

**Important context:** This installer has been written but has NOT yet been tested on real hardware or in a VM. This review is happening before its first deployment.

I would appreciate your assessment of the following areas in particular:

1. **Disk partitioning safety** — Is there adequate confirmation before destructive operations? Could the wrong disk be targeted? Are partition size calculations correct?
2. **Fstab generation** — Is UUID extraction from `blkid` handled correctly? Are filesystem types and mount options appropriate?
3. **Chroot hook execution** — Are virtual filesystems (`/dev`, `/proc`, `/sys`) mounted and unmounted correctly? Is there cleanup on failure?
4. **User creation security** — How are passwords handled? Is group membership correct for desktop use?
5. **GRUB installation** — Is the EFI/BIOS detection correct? Are the `grub-install` and `grub-mkconfig` invocations correct?
6. **TUI robustness** — Does the ncurses interface handle terminal resize, keyboard interrupts, and input validation properly?
7. **General safety** — Given that this code partitions disks and writes bootloaders, are there sufficient safeguards against data loss?

The complete source follows. There are 11 files totaling approximately 1,250 lines of Python.

---

## Source Code

### __init__.py
```python
"""Forge — InterGenOS System Installer

Uses pkm as the package deployment engine. Forge orchestrates:
disk partitioning, package extraction, config generation, post-install
hooks, bootloader installation, and user account creation.

"Forged with InterGenOS."
"""

__version__ = "0.1.0"
```

### __main__.py
```python
"""Forge — InterGenOS System Installer — Entry point.

Usage:
    forge --archives /var/lib/igos/archives [--packages /path/to/packages]
    forge --help
"""

import argparse
import sys
from pathlib import Path

from .frontend.tui import run_installer


def main():
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge — InterGenOS System Installer"
    )
    parser.add_argument("--archives", required=True,
                        help="Path to .igos.tar.gz package archives")
    parser.add_argument("--packages",
                        help="Path to packages/ directory (for post-install hooks)")
    parser.add_argument("--version", action="version",
                        version="Forge 0.1.0 (InterGenOS Installer)")

    args = parser.parse_args()

    archive_dir = Path(args.archives)
    if not archive_dir.exists():
        print(f"ERROR: Archive directory not found: {archive_dir}")
        sys.exit(1)

    packages_dir = Path(args.packages) if args.packages else None

    # Must run as root
    import os
    if os.geteuid() != 0:
        print("ERROR: Forge must be run as root.")
        print("  sudo forge --archives /path/to/archives")
        sys.exit(1)

    run_installer(str(archive_dir), str(packages_dir) if packages_dir else None)


if __name__ == "__main__":
    main()
```

### __init__.py
```python
"""Installer backend modules."""
```

### disks.py
```python
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
```

### packages.py
```python
"""Package installation for InterGenOS installer — wraps pkm."""

import os
import sys
from pathlib import Path

# Add project root so we can import pkm
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


# Package groups for installation
GROUPS = {
    "core": {
        "description": "Essential system (kernel, shell, coreutils, systemd, SSH)",
        "tiers": ["core"],
        "required": True,
    },
    "base": {
        "description": "CLI tools (htop, rsync, strace, screen)",
        "tiers": ["base"],
        "required": False,
        "default": True,
    },
    "desktop-gnome": {
        "description": "GNOME desktop environment on Wayland",
        "tiers": ["desktop"],
        "required": False,
        "default": True,
    },
    "extra": {
        "description": "Node.js, Chrome/VS Code/Claude Code helpers",
        "tiers": ["extra"],
        "required": False,
        "default": False,
    },
}


def get_archives(archive_dir):
    """Scan archive directory and return dict of {name: (version, path)}.

    Archives are named: <name>-<version>.igos.tar.gz
    """
    archive_dir = Path(archive_dir)
    archives = {}

    if not archive_dir.exists():
        return archives

    for f in sorted(archive_dir.iterdir()):
        if not f.name.endswith(".igos.tar.gz"):
            continue
        stem = f.name.replace(".igos.tar.gz", "")
        # Split on last hyphen before version number
        import re
        match = re.match(r'^(.+?)-(\d.*)$', stem)
        if match:
            name = match.group(1)
            version = match.group(2)
            archives[name] = (version, f)

    return archives


def get_group_packages(groups, archive_dir, package_dir=None):
    """Get the list of archives to install for selected groups.

    Args:
        groups: list of group names (e.g., ["core", "base", "desktop-gnome"])
        archive_dir: path to archive directory
        package_dir: path to packages/ directory (for tier mapping)

    Returns:
        list of (name, version, archive_path) tuples in install order
    """
    # Determine which tiers we need
    tiers = set()
    for group_name in groups:
        group = GROUPS.get(group_name)
        if group:
            tiers.update(group["tiers"])

    # Get available archives
    archives = get_archives(archive_dir)

    # If we have a packages directory, filter by tier
    if package_dir:
        package_dir = Path(package_dir)
        tier_packages = set()
        for tier in tiers:
            tier_dir = package_dir / tier
            if tier_dir.exists():
                for pkg_dir in tier_dir.iterdir():
                    if pkg_dir.is_dir():
                        tier_packages.add(pkg_dir.name)

        # Filter archives to only include packages in requested tiers
        filtered = []
        for name, (version, path) in sorted(archives.items()):
            if name in tier_packages:
                filtered.append((name, version, path))
        return filtered
    else:
        # No tier filtering — return all archives
        return [(name, ver, path) for name, (ver, path) in sorted(archives.items())]


def install_packages(target, archive_dir, groups, package_dir=None,
                     progress_callback=None):
    """Install packages to a target root filesystem.

    Args:
        target: target root path (e.g., /mnt/target)
        archive_dir: path to .igos.tar.gz archives
        groups: list of group names to install
        package_dir: path to packages/ for tier mapping
        progress_callback: fn(current, total, name) called per package

    Returns:
        (success_count, fail_count, failed_packages)
    """
    packages = get_group_packages(groups, archive_dir, package_dir)
    total = len(packages)

    if total == 0:
        return 0, 0, []

    # Create pkm database on the target
    db_path = Path(target) / "var" / "lib" / "igos" / "pkm.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = PackageDB(str(db_path))
    installer = PackageInstaller(db, root=target)

    success = 0
    failed = []

    for i, (name, version, archive_path) in enumerate(packages, 1):
        if progress_callback:
            progress_callback(i, total, name)

        ok, msg = installer.install(name, archive_path=str(archive_path))
        if ok:
            success += 1
        else:
            failed.append((name, msg))

    db.close()
    return success, len(failed), failed
```

### config.py
```python
"""System configuration generator for InterGenOS installer."""

import os
import subprocess
from pathlib import Path


def generate_fstab(target, partitions):
    """Generate /etc/fstab from partition UUIDs."""
    fstab_path = Path(target) / "etc" / "fstab"
    fstab_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# /etc/fstab — InterGenOS",
        "# Generated by InterGenOS installer",
        "# <file system>  <mount point>  <type>  <options>         <dump>  <pass>",
    ]

    # Root partition
    root_uuid = _get_uuid(partitions["root"])
    if root_uuid:
        lines.append(f"UUID={root_uuid}  /              ext4    defaults          1       1")
    else:
        lines.append(f"{partitions['root']}  /              ext4    defaults          1       1")

    # EFI System Partition
    if partitions.get("efi") and partitions.get("esp"):
        esp_uuid = _get_uuid(partitions["esp"])
        if esp_uuid:
            lines.append(f"UUID={esp_uuid}  /boot/efi      vfat    defaults          0       2")
        else:
            lines.append(f"{partitions['esp']}  /boot/efi      vfat    defaults          0       2")

    fstab_path.write_text("\n".join(lines) + "\n")


def generate_hostname(target, hostname):
    """Generate /etc/hostname and /etc/hosts."""
    etc = Path(target) / "etc"
    etc.mkdir(parents=True, exist_ok=True)

    (etc / "hostname").write_text(f"{hostname}\n")

    hosts = (
        f"# /etc/hosts — InterGenOS\n"
        f"\n"
        f"127.0.0.1    localhost\n"
        f"127.0.1.1    {hostname}.localdomain {hostname}\n"
        f"::1          localhost ip6-localhost ip6-loopback\n"
        f"ff02::1      ip6-allnodes\n"
        f"ff02::2      ip6-allrouters\n"
    )
    (etc / "hosts").write_text(hosts)


def generate_locale(target, locale="en_US.UTF-8"):
    """Generate /etc/locale.conf."""
    etc = Path(target) / "etc"
    (etc / "locale.conf").write_text(f"LANG={locale}\n")


def generate_vconsole(target, keymap="us"):
    """Generate /etc/vconsole.conf."""
    etc = Path(target) / "etc"
    (etc / "vconsole.conf").write_text(
        f"KEYMAP={keymap}\n"
        f"FONT=Lat2-Terminus16\n"
    )


def set_timezone(target, timezone="UTC"):
    """Set timezone via /etc/localtime symlink."""
    localtime = Path(target) / "etc" / "localtime"
    zoneinfo = Path(target) / "usr" / "share" / "zoneinfo" / timezone

    if localtime.exists() or localtime.is_symlink():
        localtime.unlink()

    if zoneinfo.exists():
        localtime.symlink_to(f"/usr/share/zoneinfo/{timezone}")
    else:
        # Fallback to UTC
        localtime.symlink_to("/usr/share/zoneinfo/UTC")


def generate_network(target):
    """Generate systemd-networkd DHCP config."""
    netdir = Path(target) / "etc" / "systemd" / "network"
    netdir.mkdir(parents=True, exist_ok=True)

    (netdir / "10-dhcp.network").write_text(
        "[Match]\n"
        "Name=en*\n"
        "\n"
        "[Network]\n"
        "DHCP=yes\n"
    )

    # DNS via systemd-resolved
    resolv = Path(target) / "etc" / "resolv.conf"
    if resolv.exists() or resolv.is_symlink():
        resolv.unlink()
    resolv.symlink_to("/run/systemd/resolve/stub-resolv.conf")


def generate_os_release(target):
    """Generate /etc/os-release and related identity files."""
    etc = Path(target) / "etc"

    (etc / "os-release").write_text(
        'NAME="InterGenOS"\n'
        'VERSION="1.0-dev (Revival)"\n'
        'ID=intergenos\n'
        'ID_LIKE=lfs\n'
        'VERSION_ID=1.0\n'
        'VERSION_CODENAME=revival\n'
        'PRETTY_NAME="InterGenOS 1.0-dev (Revival)"\n'
        'HOME_URL="https://github.com/InterGenJLU/intergenos"\n'
        'BUG_REPORT_URL="https://github.com/InterGenJLU/intergenos/issues"\n'
    )

    (etc / "igos-release").write_text("1.0-dev\n")


def generate_branding(target):
    """Generate /etc/issue and /etc/motd branding files."""
    etc = Path(target) / "etc"

    (etc / "issue").write_text(
        "\n"
        "  InterGenOS 1.0-dev (Revival)\n"
        "  Kernel \\r on \\m (\\l)\n"
        "\n"
    )

    (etc / "motd").write_text(
        "\n"
        '  Welcome to InterGenOS\n'
        '  "A system you understand, can modify, and can trust."\n'
        "\n"
        "  Documentation:  https://github.com/InterGenJLU/intergenos\n"
        "  Report issues:  https://github.com/InterGenJLU/intergenos/issues\n"
        "\n"
    )


def generate_grub_defaults(target, partitions):
    """Generate /etc/default/grub."""
    etc_default = Path(target) / "etc" / "default"
    etc_default.mkdir(parents=True, exist_ok=True)

    root_uuid = _get_uuid(partitions["root"])
    root_arg = f"root=UUID={root_uuid}" if root_uuid else f"root={partitions['root']}"

    (etc_default / "grub").write_text(
        "# GRUB defaults for InterGenOS\n"
        "GRUB_DEFAULT=0\n"
        "GRUB_TIMEOUT=5\n"
        'GRUB_DISTRIBUTOR="InterGenOS"\n'
        'GRUB_CMDLINE_LINUX_DEFAULT=""\n'
        f'GRUB_CMDLINE_LINUX="{root_arg}"\n'
        "GRUB_DISABLE_OS_PROBER=true\n"
    )


def generate_all(target, partitions, hostname="intergenos",
                 locale="en_US.UTF-8", keymap="us", timezone="UTC"):
    """Generate all system configuration files."""
    generate_fstab(target, partitions)
    generate_hostname(target, hostname)
    generate_locale(target, locale)
    generate_vconsole(target, keymap)
    set_timezone(target, timezone)
    generate_network(target)
    generate_os_release(target)
    generate_branding(target)
    generate_grub_defaults(target, partitions)


def _get_uuid(device):
    """Get filesystem UUID for a device."""
    result = subprocess.run(
        ["blkid", "-s", "UUID", "-o", "value", device],
        capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None
```

### bootloader.py
```python
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
```

### hooks.py
```python
"""Post-install hook orchestration for InterGenOS installer."""

import os
import subprocess
from pathlib import Path


def mount_virtual_fs(target):
    """Mount virtual filesystems for chroot operations."""
    target = str(target)

    mounts = [
        (f"mount --bind /dev {target}/dev", f"{target}/dev"),
        (f"mount --bind /dev/pts {target}/dev/pts", f"{target}/dev/pts"),
        (f"mount -t proc proc {target}/proc", f"{target}/proc"),
        (f"mount -t sysfs sysfs {target}/sys", f"{target}/sys"),
        (f"mount -t tmpfs tmpfs {target}/run", f"{target}/run"),
    ]

    for cmd, mountpoint in mounts:
        os.makedirs(mountpoint, exist_ok=True)
        subprocess.run(cmd, shell=True, capture_output=True)


def unmount_virtual_fs(target):
    """Unmount virtual filesystems from target."""
    target = str(target)

    for sub in ["run", "sys", "proc", "dev/pts", "dev"]:
        subprocess.run(f"umount {target}/{sub}",
                       shell=True, capture_output=True)


def run_chroot(target, command):
    """Run a command inside a chroot of the target filesystem."""
    result = subprocess.run(
        ["chroot", str(target), "/bin/bash", "-c", command],
        capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr


def run_post_install_hooks(target, packages_dir, progress_callback=None):
    """Run post_install() hooks for all packages that have them.

    Scans the packages directory for build.sh files with post_install
    functions, then executes them inside a chroot of the target.

    Args:
        target: target root path
        packages_dir: path to packages/ directory (with tier subdirs)
        progress_callback: fn(current, total, name) called per hook
    """
    target = Path(target)
    packages_dir = Path(packages_dir)

    # Find all packages with post_install hooks
    hooks = []
    for tier_dir in sorted(packages_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            if not pkg_dir.is_dir():
                continue
            build_sh = pkg_dir / "build.sh"
            if not build_sh.exists():
                continue
            # Check if build.sh contains post_install function
            content = build_sh.read_text()
            if "post_install()" in content or "post_install ()" in content:
                # Read version from package.yml
                version = ""
                yml = pkg_dir / "package.yml"
                if yml.exists():
                    for line in yml.read_text().splitlines():
                        if line.startswith("version:"):
                            version = line.split(":", 1)[1].strip().strip('"\'')
                            break
                hooks.append({
                    "name": pkg_dir.name,
                    "tier": tier_dir.name,
                    "version": version,
                    "build_sh": str(build_sh),
                })

    total = len(hooks)
    if total == 0:
        return 0

    # Mount virtual filesystems for chroot
    mount_virtual_fs(target)

    try:
        # Copy packages directory into target for hook access
        target_pkg_dir = target / "tmp" / "installer-packages"
        subprocess.run(
            ["cp", "-a", str(packages_dir), str(target_pkg_dir)],
            capture_output=True
        )

        executed = 0
        for i, hook in enumerate(hooks, 1):
            if progress_callback:
                progress_callback(i, total, hook["name"])

            # Build the chroot command
            pkg_path = f"/tmp/installer-packages/{hook['tier']}/{hook['name']}/build.sh"
            cmd = (
                f"export PKG_VERSION='{hook['version']}' && "
                f"export version='{hook['version']}' && "
                f"source {pkg_path} && "
                f"post_install"
            )

            rc, stdout, stderr = run_chroot(target, cmd)
            if rc == 0:
                executed += 1
            # Don't fail on hook errors — some hooks expect services
            # that aren't running yet (systemctl, etc.)

        # Clean up
        subprocess.run(["rm", "-rf", str(target_pkg_dir)], capture_output=True)

    finally:
        unmount_virtual_fs(target)

    return executed
```

### users.py
```python
"""User account creation for InterGenOS installer."""

import subprocess
from pathlib import Path

from .hooks import mount_virtual_fs, unmount_virtual_fs, run_chroot


def set_root_password(target, password):
    """Set the root password on the target system."""
    mount_virtual_fs(target)
    try:
        run_chroot(target, f"echo 'root:{password}' | chpasswd")
        # Remove password expiry for initial setup
        run_chroot(target, "passwd -x 99999 root")
    finally:
        unmount_virtual_fs(target)


def create_user(target, username, password, groups=None):
    """Create a user account on the target system.

    Args:
        target: target root path
        username: login name
        password: password (plain text — chpasswd handles hashing)
        groups: list of supplementary groups (default: wheel, audio, video)
    """
    if groups is None:
        groups = ["wheel", "audio", "video", "cdrom", "input"]

    mount_virtual_fs(target)
    try:
        # Create group 'wheel' if it doesn't exist (for sudo)
        run_chroot(target, "getent group wheel >/dev/null 2>&1 || groupadd wheel")

        # Create user with home directory
        group_str = ",".join(groups)
        rc, stdout, stderr = run_chroot(target,
            f"useradd -m -G {group_str} -s /bin/bash {username}"
        )
        if rc != 0 and "already exists" not in stderr:
            raise RuntimeError(f"Failed to create user {username}: {stderr}")

        # Set password
        run_chroot(target, f"echo '{username}:{password}' | chpasswd")

        # Enable sudo for wheel group (if sudoers exists)
        sudoers = Path(target) / "etc" / "sudoers"
        if sudoers.exists():
            content = sudoers.read_text()
            if "# %wheel" in content:
                content = content.replace("# %wheel ALL=(ALL:ALL) ALL",
                                          "%wheel ALL=(ALL:ALL) ALL")
                sudoers.write_text(content)

    finally:
        unmount_virtual_fs(target)


def enable_services(target):
    """Enable essential systemd services on the target."""
    mount_virtual_fs(target)
    try:
        services = [
            "systemd-networkd.service",
            "systemd-resolved.service",
            "sshd.service",
        ]
        for svc in services:
            run_chroot(target, f"systemctl enable {svc} 2>/dev/null || true")

        # Enable serial console for VM/server use
        run_chroot(target,
            "ln -sf /usr/lib/systemd/system/serial-getty@.service "
            "/etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service"
        )
    finally:
        unmount_virtual_fs(target)
```

### __init__.py
```python
"""Installer frontend modules."""
```

### tui.py
```python
"""Forge — InterGenOS System Installer — Text User Interface (ncurses).

Phase 1 installer UI. Works over SSH, serial console, or bare TTY.
Guides the user through: disk selection → configuration → install → done.
"""

import curses
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.backend import disks, packages, config, bootloader, hooks, users


class InstallerTUI:
    """Text-based installer interface using curses."""

    def __init__(self, stdscr, archive_dir, packages_dir=None):
        self.stdscr = stdscr
        self.archive_dir = archive_dir
        self.packages_dir = packages_dir

        # User selections
        self.selected_disk = None
        self.partitions = None
        self.hostname = "intergenos"
        self.timezone = "UTC"
        self.locale = "en_US.UTF-8"
        self.keymap = "us"
        self.root_password = ""
        self.username = ""
        self.user_password = ""
        self.selected_groups = ["core", "base", "desktop-gnome"]
        self.target = "/mnt/target"

        # Curses setup
        curses.curs_set(0)
        curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)

    def run(self):
        """Run the installer flow."""
        if not self.screen_welcome():
            return
        if not self.screen_disk():
            return
        if not self.screen_config():
            return
        if not self.screen_groups():
            return
        if not self.screen_confirm():
            return
        self.screen_install()
        self.screen_done()

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def clear(self):
        self.stdscr.clear()

    def header(self, title):
        """Draw the standard header."""
        h, w = self.stdscr.getmaxyx()
        self.stdscr.attron(curses.color_pair(5))
        self.stdscr.addstr(0, 0, " " * w)
        header_text = f" Forge — {title}"
        self.stdscr.addstr(0, 0, header_text[:w-1])
        self.stdscr.attroff(curses.color_pair(5))

    def message(self, row, text, color=0):
        """Display a message at the given row."""
        h, w = self.stdscr.getmaxyx()
        if color:
            self.stdscr.attron(curses.color_pair(color))
        self.stdscr.addstr(row, 2, text[:w-4])
        if color:
            self.stdscr.attroff(curses.color_pair(color))

    def prompt(self, row, text, default=""):
        """Get text input from the user."""
        curses.curs_set(1)
        curses.echo()
        self.message(row, f"{text} [{default}]: ")
        h, w = self.stdscr.getmaxyx()
        prompt_len = len(f"{text} [{default}]: ") + 2
        self.stdscr.move(row, prompt_len)
        value = self.stdscr.getstr(row, prompt_len, w - prompt_len - 2).decode().strip()
        curses.noecho()
        curses.curs_set(0)
        return value if value else default

    def prompt_password(self, row, text):
        """Get password input (hidden)."""
        curses.curs_set(1)
        curses.noecho()
        self.message(row, f"{text}: ")
        h, w = self.stdscr.getmaxyx()
        prompt_len = len(f"{text}: ") + 2
        self.stdscr.move(row, prompt_len)
        value = self.stdscr.getstr(row, prompt_len, w - prompt_len - 2).decode().strip()
        curses.curs_set(0)
        return value

    def wait_key(self, row, text="Press any key to continue..."):
        self.message(row, text, color=4)
        self.stdscr.refresh()
        self.stdscr.getch()

    def yes_no(self, row, text, default=True):
        """Ask a yes/no question."""
        default_str = "Y/n" if default else "y/N"
        self.message(row, f"{text} [{default_str}]: ")
        self.stdscr.refresh()
        key = self.stdscr.getch()
        if key in (ord('y'), ord('Y')):
            return True
        elif key in (ord('n'), ord('N')):
            return False
        return default

    # ------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------

    def screen_welcome(self):
        """Welcome screen — introduce InterGenOS."""
        self.clear()
        self.header("Welcome")
        self.message(3, "Welcome to Forge — the InterGenOS Installer", color=1)
        self.message(5, '"A system you understand, can modify, and can trust."')
        self.message(7, "This installer will guide you through setting up InterGenOS")
        self.message(8, "on your computer. The installation process will:")
        self.message(10, "  1. Partition and format a disk")
        self.message(11, "  2. Install system packages from pre-built archives")
        self.message(12, "  3. Configure your system (hostname, users, network)")
        self.message(13, "  4. Install the GRUB bootloader")
        self.message(15, "Your existing data on the selected disk WILL BE ERASED.", color=3)
        self.message(17, "Press ENTER to continue, or 'q' to quit.")
        self.stdscr.refresh()

        key = self.stdscr.getch()
        return key != ord('q')

    def screen_disk(self):
        """Disk selection screen."""
        self.clear()
        self.header("Disk Selection")

        available_disks = disks.detect_disks()
        if not available_disks:
            self.message(3, "ERROR: No disks detected!", color=3)
            self.wait_key(5)
            return False

        self.message(3, "Available disks:", color=1)
        row = 5
        for i, disk in enumerate(available_disks):
            label = f"  {i+1}. {disk.path} — {disk.size_human} — {disk.model}"
            if disk.removable:
                label += " [removable]"
            self.message(row, label)
            row += 1

        row += 1
        choice = self.prompt(row, "Select disk number", "1")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(available_disks):
                self.selected_disk = available_disks[idx]
            else:
                self.message(row + 2, "Invalid selection", color=3)
                self.wait_key(row + 3)
                return False
        except ValueError:
            self.message(row + 2, "Invalid input", color=3)
            self.wait_key(row + 3)
            return False

        row += 2
        efi = disks.is_efi()
        mode = "EFI" if efi else "BIOS"
        self.message(row, f"Boot mode: {mode}", color=2)
        row += 1
        self.message(row, f"WARNING: ALL data on {self.selected_disk.path} will be erased!", color=3)
        row += 1
        if not self.yes_no(row, "Proceed with partitioning?", default=False):
            return False

        return True

    def screen_config(self):
        """System configuration screen."""
        self.clear()
        self.header("System Configuration")

        self.message(3, "Configure your InterGenOS system:", color=1)

        self.hostname = self.prompt(5, "Hostname", self.hostname)
        self.timezone = self.prompt(7, "Timezone", self.timezone)
        self.locale = self.prompt(9, "Locale", self.locale)

        self.message(11, "Root password:", color=1)
        while True:
            self.root_password = self.prompt_password(12, "  Enter password")
            confirm = self.prompt_password(13, "  Confirm password")
            if self.root_password == confirm and self.root_password:
                break
            self.message(14, "Passwords don't match or are empty. Try again.", color=3)
            self.stdscr.refresh()

        self.message(16, "Create a user account:", color=1)
        self.username = self.prompt(17, "  Username", "")
        if self.username:
            while True:
                self.user_password = self.prompt_password(18, "  Enter password")
                confirm = self.prompt_password(19, "  Confirm password")
                if self.user_password == confirm and self.user_password:
                    break
                self.message(20, "Passwords don't match or are empty. Try again.", color=3)
                self.stdscr.refresh()

        return True

    def screen_groups(self):
        """Package group selection screen."""
        self.clear()
        self.header("Package Selection")

        self.message(3, "Select package groups to install:", color=1)

        row = 5
        group_list = list(packages.GROUPS.items())
        for i, (name, info) in enumerate(group_list):
            selected = name in self.selected_groups
            marker = "[X]" if selected else "[ ]"
            req = " (required)" if info["required"] else ""
            self.message(row, f"  {marker} {name:20s} — {info['description']}{req}")
            row += 1

        row += 1
        self.message(row, "Toggle with number keys, ENTER to continue:", color=4)
        self.stdscr.refresh()

        # Simple toggle — for now just accept defaults
        key = self.stdscr.getch()
        return True

    def screen_confirm(self):
        """Confirmation screen before installation."""
        self.clear()
        self.header("Confirm Installation")

        self.message(3, "Please review your selections:", color=1)
        self.message(5, f"  Disk:      {self.selected_disk.path} ({self.selected_disk.size_human})")
        self.message(6, f"  Boot mode: {'EFI' if disks.is_efi() else 'BIOS'}")
        self.message(7, f"  Hostname:  {self.hostname}")
        self.message(8, f"  Timezone:  {self.timezone}")
        self.message(9, f"  Locale:    {self.locale}")
        self.message(10, f"  User:      {self.username or '(none — root only)'}")
        self.message(11, f"  Groups:    {', '.join(self.selected_groups)}")

        self.message(13, "ALL DATA ON THE SELECTED DISK WILL BE ERASED.", color=3)

        if not self.yes_no(15, "Begin installation?", default=False):
            return False

        return True

    def screen_install(self):
        """Installation progress screen."""
        self.clear()
        self.header("Installing")

        row = 3

        # Step 1: Partition
        self.message(row, "Partitioning disk...", color=4)
        self.stdscr.refresh()
        try:
            efi = disks.is_efi()
            self.partitions = disks.partition_disk(self.selected_disk.path, efi=efi)
            self.message(row, "Partitioning disk... done", color=2)
        except Exception as e:
            self.message(row, f"Partitioning failed: {e}", color=3)
            self.wait_key(row + 2)
            return
        row += 1

        # Step 2: Mount
        self.message(row, "Mounting filesystems...", color=4)
        self.stdscr.refresh()
        disks.mount_target(self.partitions, self.target)
        self.message(row, "Mounting filesystems... done", color=2)
        row += 1

        # Step 3: Install packages
        self.message(row, "Installing packages...", color=4)
        self.stdscr.refresh()
        progress_row = row + 1

        def progress_cb(current, total, name):
            h, w = self.stdscr.getmaxyx()
            pct = current * 100 // total
            bar_width = 30
            filled = current * bar_width // total
            bar = "█" * filled + "░" * (bar_width - filled)
            text = f"  [{bar}] {pct}% — {name} ({current}/{total})"
            self.stdscr.addstr(progress_row, 2, text[:w-4])
            self.stdscr.clrtoeol()
            self.stdscr.refresh()

        success, fails, failed = packages.install_packages(
            self.target, self.archive_dir, self.selected_groups,
            self.packages_dir, progress_callback=progress_cb
        )
        self.message(row, f"Installing packages... {success} installed, {fails} failed", color=2)
        row = progress_row + 1

        # Step 4: Generate config
        self.message(row, "Generating system configuration...", color=4)
        self.stdscr.refresh()
        config.generate_all(
            self.target, self.partitions,
            hostname=self.hostname, locale=self.locale,
            keymap=self.keymap, timezone=self.timezone
        )
        self.message(row, "Generating system configuration... done", color=2)
        row += 1

        # Step 5: Post-install hooks
        if self.packages_dir:
            self.message(row, "Running post-install hooks...", color=4)
            self.stdscr.refresh()
            hook_count = hooks.run_post_install_hooks(
                self.target, self.packages_dir,
                progress_callback=lambda c, t, n: None
            )
            self.message(row, f"Running post-install hooks... {hook_count} executed", color=2)
            row += 1

        # Step 6: User accounts
        self.message(row, "Setting up user accounts...", color=4)
        self.stdscr.refresh()
        users.set_root_password(self.target, self.root_password)
        if self.username:
            users.create_user(self.target, self.username, self.user_password)
        users.enable_services(self.target)
        self.message(row, "Setting up user accounts... done", color=2)
        row += 1

        # Step 7: Bootloader
        self.message(row, "Installing GRUB bootloader...", color=4)
        self.stdscr.refresh()
        try:
            bootloader.install_grub(self.target, self.selected_disk.path, self.partitions)
            self.message(row, "Installing GRUB bootloader... done", color=2)
        except Exception as e:
            self.message(row, f"Bootloader installation failed: {e}", color=3)
        row += 1

        # Step 8: Unmount
        self.message(row, "Unmounting filesystems...", color=4)
        self.stdscr.refresh()
        disks.unmount_target(self.target)
        self.message(row, "Unmounting filesystems... done", color=2)
        row += 2

        self.message(row, "Installation complete!", color=2)
        self.wait_key(row + 2)

    def screen_done(self):
        """Completion screen."""
        self.clear()
        self.header("Installation Complete")

        self.message(3, "InterGenOS has been forged successfully!", color=2)
        self.message(5, "You can now remove the installation media and reboot.")
        self.message(7, f"  Hostname: {self.hostname}")
        if self.username:
            self.message(8, f"  User:     {self.username}")
        self.message(9, f"  SSH:      Enabled (port 22)")
        self.message(11, "On first boot:")
        self.message(12, "  - Log in as root or your user account")
        self.message(13, "  - Run 'sudo igos-install-chrome' for Google Chrome")
        self.message(14, "  - Run 'sudo igos-install-vscode' for VS Code")
        self.message(15, "  - Run 'igos-install-claude-code' for Claude Code")
        self.message(17, '"A system you understand, can modify, and can trust."', color=1)
        self.wait_key(20, "Press any key to exit the installer.")


def run_installer(archive_dir, packages_dir=None):
    """Entry point — launch the TUI installer."""
    def _main(stdscr):
        tui = InstallerTUI(stdscr, archive_dir, packages_dir)
        tui.run()

    curses.wrapper(_main)
```

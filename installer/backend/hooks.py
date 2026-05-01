"""Post-install hook orchestration for InterGenOS installer."""

import os
import subprocess
from pathlib import Path


def mount_virtual_fs(target):
    """Mount virtual filesystems for chroot operations."""
    target = str(target)

    mounts = [
        (["mount", "--bind", "/dev", f"{target}/dev"], f"{target}/dev"),
        (["mount", "--bind", "/dev/pts", f"{target}/dev/pts"], f"{target}/dev/pts"),
        (["mount", "-t", "proc", "proc", f"{target}/proc"], f"{target}/proc"),
        (["mount", "-t", "sysfs", "sysfs", f"{target}/sys"], f"{target}/sys"),
        (["mount", "-t", "tmpfs", "tmpfs", f"{target}/run"], f"{target}/run"),
    ]

    for cmd, mountpoint in mounts:
        os.makedirs(mountpoint, exist_ok=True)
        subprocess.run(cmd, capture_output=True, check=True)


def unmount_virtual_fs(target):
    """Unmount virtual filesystems from target (reverse order)."""
    target = str(target)

    for sub in ["run", "sys", "proc", "dev/pts", "dev"]:
        subprocess.run(["umount", f"{target}/{sub}"],
                       capture_output=True)


def mount_efivars(target):
    """Bind-mount host /sys/firmware/efi/efivars into chroot if host is EFI.

    Needed so efibootmgr can update the firmware boot order from inside
    the chroot. Returns True if the mount succeeded (or was already there),
    False otherwise — callers use this to decide whether efibootmgr can
    succeed or must defer to first boot.
    """
    target = str(target)
    host_path = "/sys/firmware/efi/efivars"
    if not os.path.isdir(host_path):
        return False

    chroot_path = f"{target}{host_path}"
    os.makedirs(chroot_path, exist_ok=True)

    # Already mounted? Idempotent.
    check = subprocess.run(
        ["mountpoint", "-q", chroot_path], capture_output=True
    )
    if check.returncode == 0:
        return True

    result = subprocess.run(
        ["mount", "--bind", host_path, chroot_path],
        capture_output=True,
    )
    return result.returncode == 0


def unmount_efivars(target):
    """Unmount efivars bind from chroot; no-op if not mounted."""
    target = str(target)
    subprocess.run(
        ["umount", f"{target}/sys/firmware/efi/efivars"],
        capture_output=True,
    )


def run_chroot(target, command):
    """Run a command inside a chroot of the target filesystem."""
    result = subprocess.run(
        ["chroot", str(target), "/bin/bash", "-c", command],
        capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr


def run_chroot_stdin(target, command, input_data):
    """Run a command inside a chroot, feeding data via stdin.

    Use this instead of shell echo pipes for sensitive data (passwords)
    to avoid process table exposure.
    """
    result = subprocess.run(
        ["chroot", str(target), "/bin/bash", "-c", command],
        input=input_data, capture_output=True, text=True
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
            import shlex
            pkg_path = f"/tmp/installer-packages/{shlex.quote(hook['tier'])}/{shlex.quote(hook['name'])}/build.sh"
            cmd = (
                f"export PKG_VERSION={shlex.quote(hook['version'])} && "
                f"export version={shlex.quote(hook['version'])} && "
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

"""Post-install hook orchestration for InterGenOS installer."""

import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


def mount_virtual_fs(target):
    """Mount virtual filesystems for chroot operations.

    Mounts /dev (bind), /dev/pts (bind), /proc, /sys, /run in that order.
    On partial failure (any mount raises), unmounts the ones that succeeded
    in reverse order before re-raising — leaves the system in a known state
    so a retry doesn't hit an "already mounted" cascade.
    """
    target = str(target)

    mounts = [
        (["mount", "--bind", "/dev", f"{target}/dev"], f"{target}/dev"),
        (["mount", "--bind", "/dev/pts", f"{target}/dev/pts"], f"{target}/dev/pts"),
        (["mount", "-t", "proc", "proc", f"{target}/proc"], f"{target}/proc"),
        (["mount", "-t", "sysfs", "sysfs", f"{target}/sys"], f"{target}/sys"),
        (["mount", "-t", "tmpfs", "tmpfs", f"{target}/run"], f"{target}/run"),
    ]

    completed = []
    for cmd, mountpoint in mounts:
        os.makedirs(mountpoint, exist_ok=True)
        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError:
            for done in reversed(completed):
                subprocess.run(["umount", done], capture_output=True)
            raise
        completed.append(mountpoint)


def unmount_virtual_fs(target):
    """Unmount virtual filesystems from target (reverse order).

    Best-effort: a single umount failure (e.g., busy) is logged as a warning
    but doesn't halt cleanup of subsequent mounts. Halting on first failure
    would let one stuck mount block cleanup of the others, which is worse
    than the current "log + continue" behavior. Caller cannot distinguish
    "all clean" from "partial busy" — the warning log is the channel for that.
    """
    target = str(target)
    for sub in ["run", "sys", "proc", "dev/pts", "dev"]:
        path = f"{target}/{sub}"
        result = subprocess.run(
            ["umount", path], capture_output=True, text=True
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            log.warning("umount %s returncode=%d (%s)",
                        path, result.returncode, stderr)


@contextmanager
def virtual_fs(target):
    """Context-manager form of mount_virtual_fs / unmount_virtual_fs.

    Use in callers that want the cleaner mount-do-unmount shape:

        with virtual_fs(target):
            run_chroot(target, "...")

    Equivalent to:

        mount_virtual_fs(target)
        try:
            ...
        finally:
            unmount_virtual_fs(target)

    Partial-mount rollback is handled by mount_virtual_fs itself; the
    finally block here only runs after successful mount.
    """
    mount_virtual_fs(target)
    try:
        yield
    finally:
        unmount_virtual_fs(target)


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

    with virtual_fs(target):
        # Copy packages directory into target for hook access. Returncode-
        # checked: a silent cp failure (disk full / permission denied) means
        # no hook source files reach the chroot, so every per-hook source
        # would fail with file-not-found; we skip the hooks loop entirely
        # and surface the failure via progress_callback so the orchestrator
        # frontend can render the warning.
        target_pkg_dir = target / "tmp" / "installer-packages"
        cp_result = subprocess.run(
            ["cp", "-a", str(packages_dir), str(target_pkg_dir)],
            capture_output=True, text=True,
        )
        if cp_result.returncode != 0:
            stderr = (cp_result.stderr or "").strip()
            log.warning("cp -a packages-dir failed: %s", stderr)
            if progress_callback:
                progress_callback(
                    0, total,
                    "warning: cannot copy packages — hooks skipped",
                )
            return 0

        executed = 0
        try:
            for i, hook in enumerate(hooks, 1):
                if progress_callback:
                    progress_callback(i, total, hook["name"])

                # Build the chroot command
                import shlex
                pkg_path = (
                    f"/tmp/installer-packages/"
                    f"{shlex.quote(hook['tier'])}/"
                    f"{shlex.quote(hook['name'])}/build.sh"
                )
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

        finally:
            subprocess.run(
                ["rm", "-rf", str(target_pkg_dir)], capture_output=True
            )

    return executed

"""User account creation for InterGenOS installer."""

import shlex
import subprocess
from pathlib import Path

from .hooks import mount_virtual_fs, unmount_virtual_fs, run_chroot, run_chroot_stdin


def set_root_password(target, password):
    """Set the root password on the target system."""
    mount_virtual_fs(target)
    try:
        # Feed password via stdin to avoid process table exposure
        run_chroot_stdin(target, "chpasswd", f"root:{password}\n")
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
            f"useradd -m -G {shlex.quote(group_str)} -s /bin/bash {shlex.quote(username)}"
        )
        if rc != 0 and "already exists" not in stderr:
            raise RuntimeError(f"Failed to create user {username}: {stderr}")

        # Set password via stdin (avoids process table exposure)
        run_chroot_stdin(target, "chpasswd", f"{username}:{password}\n")

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

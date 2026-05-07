"""User account creation for InterGenOS installer."""

import logging
import re
import shlex
import subprocess
from pathlib import Path

from .hooks import mount_virtual_fs, unmount_virtual_fs, run_chroot, run_chroot_stdin

log = logging.getLogger(__name__)

# Anchored regex for the canonical commented `%wheel` line in /etc/sudoers.
# Tolerates arbitrary whitespace between tokens; closes the brittle
# fixed-string-replace that silently no-op'd if upstream sudo shipped with
# tab spacing or extra whitespace, leaving sudo silently disabled for the
# wheel group and locking the user out of administrative recovery.
_SUDOERS_WHEEL_COMMENTED_RE = re.compile(
    r'^#\s*%wheel\s+ALL=\(ALL:ALL\)\s+ALL\s*$',
    re.MULTILINE,
)


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

        # Enable sudo for wheel group (if sudoers exists). Stage to
        # /etc/sudoers.new + run visudo -c -f for syntax-check before
        # committing — a malformed sudoers locks the user out of sudo
        # entirely. If verification fails, leave sudoers unchanged and
        # log a warning rather than fail the install (user can still
        # gain root via initial password and hand-edit sudoers).
        sudoers = Path(target) / "etc" / "sudoers"
        if sudoers.exists():
            content = sudoers.read_text()
            new_content = _SUDOERS_WHEEL_COMMENTED_RE.sub(
                '%wheel ALL=(ALL:ALL) ALL', content
            )
            if new_content != content:
                staging = Path(target) / "etc" / "sudoers.new"
                staging.write_text(new_content)
                rc, _, stderr = run_chroot(
                    target, "visudo -c -f /etc/sudoers.new"
                )
                if rc == 0:
                    staging.replace(sudoers)
                else:
                    staging.unlink(missing_ok=True)
                    log.warning(
                        "sudoers regex-sed produced syntactically-invalid "
                        "file (visudo: %s); leaving sudoers unchanged",
                        (stderr or "").strip(),
                    )

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

"""MOK (Machine Owner Key) management for Forge installer.

Generates per-install MOK keypairs, queues enrollment via mokutil, and
provides EFI binary signing via sbsign. The MOK is the user's own key —
distinct from the InterGenOS distro signing key (which signs the repo
index and lives on hardware tokens, never installed on user machines).

MOK enrollment lifecycle:
- Each install generates a fresh MOK keypair stored under /var/lib/intergen/mok/.
- Public cert is queued for enrollment via `mokutil --import`.
- Enrollment completes at first boot when the user runs MokManager
  from the BIOS prompt and enters the password we set.
- After enrollment, the MOK pubkey lives in the kernel's secondary
  trusted keyring (CONFIG_SECONDARY_TRUSTED_KEYRING=y), allowing kernel
  modules signed with the corresponding private key (e.g., DKMS-built
  NVIDIA modules) to load under CONFIG_MODULE_SIG_FORCE=y.
"""

import re
import secrets
import subprocess
from pathlib import Path

from .hooks import (
    mount_efivars,
    unmount_efivars,   # batch 1 fix: C1 efivars mount around mokutil
    run_chroot,
    run_chroot_stdin,
)


MOK_DIR = "/var/lib/intergen/mok"
MOK_KEY_BITS = 2048  # RSA-2048 — matches kernel module signing default

# Whitelist for MOK X.509 CN. Rejects quotes, backslashes, shell metacharacters,
# and anything else that could break out of the single-quoted `-subj` arg to
# openssl req. X.509 CN doesn't need exotic chars; alnum + space + _.- covers
# every realistic machine-owner label.
_COMMON_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,64}$")


def generate_mok_keypair(target, common_name="InterGenOS Machine Owner Key"):
    """Generate a fresh MOK keypair on the target system.

    Creates an RSA-2048 X.509 self-signed cert + private key under
    /var/lib/intergen/mok/ on the target. The keypair is per-install —
    different on every machine, never reused.

    Args:
        target: target root path
        common_name: CN field for the cert subject. Must match
            ``[A-Za-z0-9 _.-]{1,64}`` to prevent shell injection into the
            openssl ``-subj`` argument.

    Returns:
        dict with keys: 'key_path', 'cert_path', 'der_path'
        (all paths are inside the chroot, e.g., /var/lib/intergen/mok/mok.key)

    Raises:
        ValueError if common_name fails the whitelist.
        RuntimeError if keypair generation fails.
    """
    if not _COMMON_NAME_RE.fullmatch(common_name):
        raise ValueError(
            f"MOK common_name must match {_COMMON_NAME_RE.pattern} "
            f"(got {common_name!r})"
        )

    target = Path(target)
    mok_dir = target / MOK_DIR.lstrip("/")
    mok_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    key_path = f"{MOK_DIR}/mok.key"
    cert_path = f"{MOK_DIR}/mok.crt"
    der_path = f"{MOK_DIR}/mok.der"

    # Generate RSA private key + self-signed X.509 cert in one openssl call
    # -nodes: no passphrase on the private key (keys live on the machine,
    # protected by filesystem perms — adding a passphrase would block
    # automated DKMS signing without solving any threat we actually face)
    openssl_cmd = (
        f"openssl req -new -x509 -newkey rsa:{MOK_KEY_BITS} "
        f"-keyout {key_path} -out {cert_path} -outform PEM "
        f"-days 36500 -nodes "
        f"-subj '/CN={common_name}/'"
    )
    rc, stdout, stderr = run_chroot(str(target), openssl_cmd)
    if rc != 0:
        raise RuntimeError(f"MOK keypair generation failed: {stderr}")

    # Convert PEM cert to DER (mokutil --import requires DER format)
    der_cmd = f"openssl x509 -in {cert_path} -outform DER -out {der_path}"
    rc, stdout, stderr = run_chroot(str(target), der_cmd)
    if rc != 0:
        raise RuntimeError(f"MOK DER conversion failed: {stderr}")

    # Lock down permissions on the private key
    chmod_cmd = f"chmod 600 {key_path} && chmod 644 {cert_path} {der_path}"
    rc, _, stderr = run_chroot(str(target), chmod_cmd)
    if rc != 0:
        raise RuntimeError(f"MOK permission lock failed: {stderr}")

    return {
        "key_path": key_path,
        "cert_path": cert_path,
        "der_path": der_path,
    }


def queue_mok_enrollment(target, der_path, password):
    """Queue MOK cert for enrollment at next boot via mokutil --import.

    The cert is staged in the EFI variable namespace; on next reboot
    the BIOS surfaces a MokManager prompt where the user enters the
    password we set here, then confirms the enrollment. After reboot
    completes, the MOK is in the kernel's trusted keyring.

    Args:
        target: target root path
        der_path: path inside chroot to the DER-encoded MOK cert
        password: enrollment password (8-256 chars, shown to user one time)

    Raises:
        RuntimeError if mokutil import fails.
        ValueError if password is invalid.
    """
    if not 8 <= len(password) <= 256:
        raise ValueError(
            f"MOK enrollment password must be 8-256 chars (got {len(password)})"
        )
    # Printable ASCII only. Control chars (NUL, newline, CR, tab) would break
    # the stdin pipe below — mokutil reads two password lines separated by \n,
    # so an embedded newline splits the password into two false reads.
    if not all(32 <= ord(c) <= 126 for c in password):
        raise ValueError(
            "MOK enrollment password must be printable ASCII only "
            "(no control chars, tabs, newlines, or non-ASCII — mokutil reads "
            "via stdin and the user must re-type at MokManager)"
        )

    # mokutil --import takes the cert path and prompts for password twice
    # via stdin. Pipe it as "password\npassword\n".
    cmd = f"mokutil --import {der_path}"
    stdin_data = f"{password}\n{password}\n"

    # Mount efivars so mokutil can write EFI variables (C1).
    # same pattern as bootloader.py:197 — the chroot needs
    # /sys/firmware/efi/efivars accessible to stage the MOK
    # enrollment for next boot.
    mount_efivars(target)

    try:
        rc, stdout, stderr = run_chroot_stdin(str(target), cmd, stdin_data)
    finally:
        unmount_efivars(target)

    if rc != 0:
        raise RuntimeError(f"mokutil --import failed: {stderr}")


def sign_efi_binary(target, binary_path, key_path, cert_path, output_path=None):
    """Sign an EFI binary (GRUB, kernel image) with an MOK key via sbsign.

    Args:
        target: target root path
        binary_path: path inside chroot to the EFI binary to sign
        key_path: path inside chroot to the signing private key (PEM)
        cert_path: path inside chroot to the signing cert (PEM)
        output_path: path inside chroot for the signed output. If None,
                     overwrites binary_path in place (sbsign --output same).

    Returns:
        Path to the signed binary (always inside chroot).

    Raises:
        RuntimeError if sbsign fails.
    """
    if output_path is None:
        output_path = binary_path

    cmd = (
        f"sbsign --key {key_path} --cert {cert_path} "
        f"--output {output_path} {binary_path}"
    )
    rc, stdout, stderr = run_chroot(str(target), cmd)
    if rc != 0:
        raise RuntimeError(f"sbsign failed for {binary_path}: {stderr}")

    return output_path


def verify_efi_signature(target, binary_path, cert_path):
    """Verify an EFI binary's signature against a cert (sbverify).

    Used by the test harness to confirm signed-chain integrity after
    install. Returns True if the binary verifies against the cert.

    Args:
        target: target root path
        binary_path: path inside chroot to the EFI binary
        cert_path: path inside chroot to the cert to verify against

    Returns:
        True if signature verifies, False otherwise.
    """
    cmd = f"sbverify --cert {cert_path} {binary_path}"
    rc, _, _ = run_chroot(str(target), cmd)
    return rc == 0


def generate_enrollment_password():
    """Generate a one-time MOK enrollment password.

    Returns a human-readable password (12 chars, alphanumeric, mixed case).
    Shown to the user once during install, used at first-boot MokManager.
    """
    # Avoid ambiguous chars (0/O, 1/l/I) for keyboard entry at MokManager
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"
    return "".join(secrets.choice(alphabet) for _ in range(12))

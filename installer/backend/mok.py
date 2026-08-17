# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""MOK (Machine Owner Key) management for Forge installer.

Generates per-install MOK keypairs, queues enrollment via mokutil, and
provides EFI binary signing via sbsign. The MOK is the user's own key —
distinct from the InterGenOS distro signing key (which signs the repo
index and lives on hardware tokens, never installed on user machines).

MOK enrollment lifecycle (the supported procedure, in order):
- The machine is installed with Secure Boot DISABLED in UEFI firmware —
  the installed boot chain is signed with a MOK the firmware does not
  trust yet.
- Each install generates a fresh MOK keypair stored under /var/lib/intergen/mok/.
- The user SETS the enrollment password in the installer frontend (GUI
  "Secure Boot enrollment" group / TUI "Secure Boot MOK password"). It is
  never generated here, never displayed back, and never logged; an empty
  value means the caller skips enrollment entirely.
- Public cert is queued for enrollment via `mokutil --import`.
- Enrollment is TRIGGERED when the user re-enables Secure Boot in UEFI
  firmware on the first reboot: that puts shim in the boot path, shim
  finds the pending enrollment, and MokManager asks for the password the
  user set. With Secure Boot left off, shim never loads and the pending
  enrollment simply waits.
- After enrollment, the MOK pubkey lives in the kernel's secondary
  trusted keyring (CONFIG_SECONDARY_TRUSTED_KEYRING=y), allowing kernel
  modules signed with the corresponding private key (e.g., DKMS-built
  NVIDIA modules) to load under CONFIG_MODULE_SIG_FORCE=y.
"""

import re
import subprocess
from pathlib import Path

from ._validators import validate_mok_password
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

    # In-target absolute paths (used by every consumer that operates
    # inside the target's chroot — bootloader signing, linux-kernel
    # UKI hook, mokutil enrollment, etc.).
    key_path = f"{MOK_DIR}/mok.key"
    cert_path = f"{MOK_DIR}/mok.crt"
    der_path = f"{MOK_DIR}/mok.der"

    # Filesystem paths (host's view of the target chroot — used by the
    # live-ISO openssl invocations below). Computed from `target` so the
    # rest of the function can stay path-symmetric with the in-target
    # consumers above.
    key_fspath = str(target / key_path.lstrip("/"))
    cert_fspath = str(target / cert_path.lstrip("/"))
    der_fspath = str(target / der_path.lstrip("/"))

    # Run openssl on the LIVE ISO (not in the target chroot) so this
    # function can be called BEFORE the target's openssl package is
    # installed. Surfaced 2026-05-27 install #27: the linux-kernel
    # post-install UKI hook needs /var/lib/intergen/mok/mok.{key,crt}
    # to exist when it fires during pkm install of linux-kernel; before
    # this refactor PHASE_MOK ran AFTER PHASE_PACKAGES, so the hook
    # could never sign with MOK at fire time. Moving the call site is
    # done in install.py; this code change makes the move safe by
    # removing the chroot dependency on target-side openssl.
    # -nodes: no passphrase on the private key (keys live on the
    # machine, protected by filesystem perms — adding a passphrase
    # would block automated DKMS signing without solving any threat
    # we actually face).
    openssl_cmd = [
        "openssl", "req", "-new", "-x509",
        "-newkey", f"rsa:{MOK_KEY_BITS}",
        "-keyout", key_fspath,
        "-out", cert_fspath,
        "-outform", "PEM",
        "-days", "36500",
        "-nodes",
        "-subj", f"/CN={common_name}/",
    ]
    result = subprocess.run(openssl_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"MOK keypair generation failed: {result.stderr}")

    # Convert PEM cert to DER (mokutil --import requires DER format)
    der_result = subprocess.run(
        ["openssl", "x509", "-in", cert_fspath, "-outform", "DER",
         "-out", der_fspath],
        capture_output=True, text=True,
    )
    if der_result.returncode != 0:
        raise RuntimeError(f"MOK DER conversion failed: {der_result.stderr}")

    # Lock down permissions on the private key. Filesystem perms apply
    # equally inside/outside the chroot so direct os.chmod works.
    import os
    os.chmod(key_fspath, 0o600)
    os.chmod(cert_fspath, 0o644)
    os.chmod(der_fspath, 0o644)

    return {
        "key_path": key_path,
        "cert_path": cert_path,
        "der_path": der_path,
    }


def queue_mok_enrollment(target, der_path, password):
    """Queue MOK cert for enrollment at next boot via mokutil --import.

    The cert is staged in the EFI variable namespace. Enrollment is
    surfaced on the next boot for which Secure Boot is ENABLED in UEFI
    firmware — shim runs MokManager, which asks for the password the user
    set during install and then confirms the enrollment. (Installs run
    with Secure Boot off, so in practice that is the boot after the user
    re-enables it; until then the staged enrollment simply waits.) After
    the enrolling reboot completes, the MOK is in the kernel's trusted
    keyring.

    Args:
        target: target root path
        der_path: path inside chroot to the DER-encoded MOK cert
        password: the enrollment password the user set in the installer
            frontend (8-256 printable-ASCII chars). Never generated here,
            never displayed back, never logged.

    Raises:
        RuntimeError if mokutil import fails.
        ValueError if password is invalid.
    """
    # Empty input is rejected at the enrollment-call layer because the caller
    # owns the skip-MOK-enrollment branch (the validator accepts empty as
    # valid input from the GUI layer where empty means "skip MOK"; here in
    # queue_mok_enrollment empty would mean caller logic error since
    # we are explicitly enrolling).
    if not password:
        raise ValueError(
            "MOK enrollment password must not be empty (caller must skip "
            "queue_mok_enrollment entirely when user leaves the MOK field "
            "blank in the GUI)"
        )
    err = validate_mok_password(password)
    if err:
        raise ValueError(err)

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

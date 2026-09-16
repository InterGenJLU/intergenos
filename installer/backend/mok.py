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

from . import trace
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


# Where the DER certificate is staged so the two recovery paths work without
# another machine (R001.3 row 37 (a), decided 2026-09-05 after the hub's own
# dropped enrolment): the EFI system partition, because MokManager's
# "Enroll key from disk" can read only FAT volumes, never the (encrypted)
# root; and a world-readable copy on the root, because the first-login
# check (the Welcomer) runs as the person, who cannot read the 0700
# /var/lib/intergen/mok/ or the 0700 ESP mount. The certificate is public.
ESP_MOK_CERT_DIR = "/boot/efi/EFI/InterGenOS"
ESP_MOK_CERT = f"{ESP_MOK_CERT_DIR}/mok.der"
PUBLIC_MOK_CERT = "/etc/intergenos/mok.der"


def stage_mok_certificate(target, der_path):
    """Copy the DER certificate to the ESP and to the public root path.

    Runs on the live system against the mounted target (plain file copies,
    no chroot). Both copies are read back and compared byte for byte with
    the source; a mismatch or a failed copy raises — a staged recovery path
    that does not match the enrolled key is worse than none. Returns the
    two in-target paths.
    """
    import os
    import shutil
    target = Path(target)
    src = target / der_path.lstrip("/")
    der = src.read_bytes()
    staged = []
    for rel in (ESP_MOK_CERT, PUBLIC_MOK_CERT):
        dst = target / rel.lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o644)
        if dst.read_bytes() != der:
            raise RuntimeError(
                f"MOK certificate staged at {rel} does not match {der_path}")
        staged.append(rel)
    trace.trace_event(
        "mok_certificate_staged", phase="bootloader",
        source=der_path, staged=staged, size=len(der),
        intent="stage the MOK certificate for 'Enroll key from disk' and the "
               "first-login enrolment check")
    return staged


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


# ---------------------------------------------------------------------------
# Prior machine owner keys: reading what the firmware already trusts, and
# offering to retire the ones this machine no longer holds a private half for.
#
# Every install generates a fresh key and enrols it. Nothing ever retired the
# old ones, so a machine that has been reinstalled several times accumulates a
# trusted key per install — measured on one fleet machine: seven of this
# project's keys enrolled, six of them belonging to disks that were installed
# over. A key stays trusted whether or not anyone still holds its private half,
# and the dates in the certificate are not checked by anything in the boot
# path, so the only way a machine's trust set ever shrinks is a deliberate
# removal.
#
# The rules this code keeps, in the order they matter:
#   - it never removes anything on its own. It reads, it reports, and it queues
#     a removal only for keys a person explicitly chose;
#   - the removal goes through the firmware's own manager, which asks the
#     person to confirm it at the same prompt, with the same password, as an
#     addition — no separate trust path is invented here;
#   - it never claims an enrolment date. The firmware records none. What it
#     shows is the certificate's own creation time, which on this system IS the
#     install that generated it, and the wording says "created";
#   - the key this install just generated is excluded by comparing SHA-1 over
#     the certificate bytes, which is what the firmware's own listing prints.
#     Comparing a differently-computed fingerprint would silently mark the live
#     key as a prior one.
OWNER_KEY_COMMON_NAME = "InterGenOS Machine Owner Key"

# Where the exported certificates land inside the target while they are read.
# Inside the target rather than the live root: the files come from the
# firmware, and a temporary directory on the target is removed with it if the
# install is abandoned.
_EXPORT_DIR = "/var/lib/intergen/mok/enrolled"


def sha1_fingerprint(der):
    """The SHA-1 of the certificate bytes, lower-case and unseparated.

    This is the value the firmware's own listing prints per enrolled key, so it
    is the value every comparison in this module uses.
    """
    import hashlib
    return hashlib.sha1(der).hexdigest()


def export_enrolled_certificates(target, runner=None):
    """Every certificate the firmware currently trusts as a machine owner key.

    Returns a list of DER byte strings, or None when the store cannot be read
    at all — an unreadable store is never reported as an empty one, because
    "no prior keys" and "could not look" must not reach a person as the same
    sentence.

    The export runs through the target's own tooling with the firmware
    variables mounted, the same way the enrolment does. It writes files and
    reads nothing else; it changes no key store.
    """
    runner = runner or run_chroot
    export_dir = Path(target) / _EXPORT_DIR.lstrip("/")
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    mount_efivars(target)
    try:
        rc, _out, _err = runner(
            str(target), f"cd {_EXPORT_DIR} && rm -f MOK-*.der && mokutil --export")
    finally:
        unmount_efivars(target)
    if rc != 0:
        return None
    ders = []
    for path in sorted(export_dir.glob("MOK-*.der")):
        try:
            ders.append(path.read_bytes())
        except OSError:
            return None
    return ders


def certificate_identity(der):
    """What a person needs to recognise one enrolled certificate.

    Returns a dict with the common name, the SHA-1 fingerprint, and the
    certificate's own validity dates as the firmware would show them, or None
    when the bytes cannot be read as a certificate. `created` is the
    certificate's start time: this project generates the key during the
    install, so it is the install's own moment — it is NOT an enrolment date,
    and no caller may present it as one, because the firmware records none.
    """
    import subprocess
    from .secureboot import certificate_common_name
    cn = certificate_common_name(der)
    if cn is None:
        return None
    try:
        proc = subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-noout", "-dates"],
            input=der, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    created = expires = None
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("notBefore="):
            created = line[len("notBefore="):].strip()
        elif line.startswith("notAfter="):
            expires = line[len("notAfter="):].strip()
    return {"common_name": cn, "sha1": sha1_fingerprint(der),
            "created": created, "expires": expires, "der": der}


def prior_owner_keys(ders, current_der):
    """This project's enrolled keys other than the one this install generated.

    Certificates with any other common name — a vendor's authority, for
    instance — are not this project's to offer for removal and are left alone.
    The current key is excluded by fingerprint, never by position or by date.
    """
    if not ders:
        return []
    current = sha1_fingerprint(current_der) if current_der else None
    prior = []
    for der in ders:
        identity = certificate_identity(der)
        if identity is None:
            continue
        if identity["common_name"] != OWNER_KEY_COMMON_NAME:
            continue
        if current is not None and identity["sha1"] == current:
            continue
        prior.append(identity)
    return prior


def queue_owner_key_removal(target, identities, password, runner=None):
    """Queue the chosen prior keys for removal at the firmware's own prompt.

    Nothing is removed here. The request waits until the machine next starts
    with Secure Boot on, where the firmware's manager lists it as a deletion
    and asks the person to confirm it with the enrolment password — the same
    prompt, the same password and the same ten-second window as an addition. If
    that prompt is missed the request is dropped and the key store is left
    exactly as it was; the first-login check is what says so afterwards.

    `identities` are records from prior_owner_keys(); an empty list is a
    programming error rather than a no-op, because "remove nothing" is the
    decline path and the caller owns it.
    """
    runner = runner or run_chroot_stdin
    if not identities:
        raise ValueError(
            "queue_owner_key_removal called with no keys — the caller owns the "
            "decline path and must not reach here when a person keeps them all")
    if not password:
        raise ValueError("removal needs the enrolment password the person set")
    err = validate_mok_password(password)
    if err:
        raise ValueError(err)

    target_path = Path(target)
    export_dir = target_path / _EXPORT_DIR.lstrip("/")
    export_dir.mkdir(parents=True, exist_ok=True)
    in_target = []
    for identity in identities:
        name = f"retire-{identity['sha1']}.der"
        (export_dir / name).write_bytes(identity["der"])
        in_target.append(f"{_EXPORT_DIR}/{name}")

    cmd = "mokutil --delete " + " ".join(in_target)
    stdin_data = f"{password}\n{password}\n"
    mount_efivars(target)
    try:
        rc, _out, stderr = runner(str(target), cmd, stdin_data)
    finally:
        unmount_efivars(target)
    if rc != 0:
        raise RuntimeError(f"mokutil --delete failed: {stderr}")
    trace.trace_event(
        "mok_prior_keys_queued_for_removal", phase="bootloader",
        count=len(identities),
        fingerprints=[i["sha1"] for i in identities],
        intent="the person chose to retire previously enrolled machine owner "
               "keys; the firmware asks them to confirm it when the machine "
               "next starts with Secure Boot on")


def record_owner_key_decision(kept, removed):
    """Record what the person decided about prior keys, including keeping them.

    A decline is recorded as deliberately as a removal: a machine that still
    trusts six old keys should say that someone was asked and said no, not go
    quiet.
    """
    trace.trace_event(
        "mok_prior_keys_decision", phase="bootloader",
        offered=len(kept) + len(removed), kept=len(kept), removed=len(removed),
        declined=not removed,
        intent="record the person's answer to the prior-key offer, whichever "
               "way it went")


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

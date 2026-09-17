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

The private half is ENCRYPTED AT REST under a passphrase the machine owner
sets in the installer, and every signing step asks for it (decided
2026-09-17). This is a different secret from the enrollment password above:
that one is typed once at the firmware's own key manager to confirm an
enrollment, this one guards the key itself for the life of the machine.

Earlier releases stored the key without a passphrase so that kernel and
driver updates could sign unattended. The consequence, measured rather than
argued, was that any process running as root could sign a boot image the
firmware trusts, so Secure Boot stopped an attacker without root and nobody
with root. Unattended kernel updates and a boot chain that resists root
cannot both hold.
"""

import contextlib
import re
import subprocess
from pathlib import Path

from . import trace
from ._validators import validate_mok_key_passphrase, validate_mok_password
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

# How the owner's passphrase reaches a tool that needs it.
#
# The rule everything here follows: never an argument, never a file. An
# argument is in the process table for every user on the machine to read while
# the command runs; a file on disk is the thing the passphrase exists to avoid.
# What is left is this process's environment and a pipe, and which of the two
# is used is decided by the tool rather than by preference — measured on the
# installed tools, 2026-09-17:
#   - openssl takes `-passin`/`-passout env:NAME` and reads NAME here.
#   - the boot-image signer (sbsign) has no option and no variable at all. It
#     loads the key through OpenSSL's default passphrase callback, which reads
#     ONE LINE from standard input when standard input is a pipe. One line per
#     invocation: a loop that signs four images needs four feeds.
#   - the module signer (the kernel's scripts/sign-file) reads one named
#     variable and does not read standard input.
_PASS_ENV = "IGOS_MOK_PASSPHRASE"


def _env_with_passphrase(passphrase, env=None, name=_PASS_ENV):
    """This process's environment plus the passphrase under `name`.

    A copy is made rather than mutating os.environ, so the value exists only
    for the child that needs it and never lingers in the installer's own
    environment where an unrelated subprocess would inherit it.
    """
    import os
    merged = dict(os.environ if env is None else env)
    merged[name] = passphrase
    return merged


@contextlib.contextmanager
def passphrase_in_environment(passphrase, name=_PASS_ENV):
    """Put the owner's passphrase in THIS process's environment, briefly.

    For the one case that cannot use a per-child environment: the package
    phase, where the signing hook is started several layers down by the package
    manager's own hook runner rather than by a call this code makes. The
    variable is removed again when the block ends, however the block ends, so
    an unrelated subprocess started later in the install does not inherit it.

    A passphrase of None is not an error here and the block simply does
    nothing: a BIOS install signs nothing and has no passphrase to hold. The
    signing steps themselves are what refuse when one is missing, because they
    are the places that know a signature was expected.
    """
    import os
    if not passphrase:
        yield
        return
    previous = os.environ.get(name)
    os.environ[name] = passphrase
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def signing_env(passphrase, env=None):
    """The environment a chrooted signing step needs, carrying the passphrase.

    The variable name is not decorative. The install trace scrubs the value of
    any environment variable whose NAME contains PASSPHRASE (among other
    markers), so naming it for what it holds is what keeps it out of the
    durable trace file. A name like IGOS_MOK_SECRET_X would be recorded in
    full.

    Raises ValueError with no passphrase: a signing step that reaches this
    point without one is about to produce an unsigned boot artefact, and that
    is a refusal rather than a degraded success.
    """
    if not passphrase:
        raise ValueError(
            "a signing step was reached without the machine owner's "
            "signing-key passphrase; the key is encrypted at rest and nothing "
            "can sign with it until the owner provides it")
    return _env_with_passphrase(passphrase, env)


def verify_key_is_encrypted(target, key_path, passphrase):
    """Prove the private key is encrypted, and that this passphrase opens it.

    Two assertions, and the first one is the one that matters. Reading the key
    with an EMPTY passphrase must FAIL: against a plain key that same command
    answers "RSA key ok", which is how the finding was measured on a real
    install. A check that only proved the passphrase works would pass on a
    plain key too, because a plain key opens under any passphrase argument.

    Raises RuntimeError when either assertion fails. The caller treats that as
    an install failure: a key that is not protected is not a key this design
    can ship, and saying so in a warning is how the previous state survived.
    """
    key_fspath = str(Path(target) / str(key_path).lstrip("/"))

    empty = subprocess.run(
        ["openssl", "rsa", "-in", key_fspath, "-check", "-noout",
         "-passin", "pass:"],
        capture_output=True, text=True)
    if empty.returncode == 0:
        raise RuntimeError(
            f"the machine owner signing key at {key_path} reads back with an "
            f"EMPTY passphrase, so it is stored unencrypted. Any process "
            f"running as root could sign a boot image this machine's firmware "
            f"trusts. The install stops here rather than shipping it.")

    opened = subprocess.run(
        ["openssl", "rsa", "-in", key_fspath, "-check", "-noout",
         "-passin", f"env:{_PASS_ENV}"],
        capture_output=True, text=True, env=_env_with_passphrase(passphrase))
    if opened.returncode != 0:
        raise RuntimeError(
            f"the machine owner signing key at {key_path} is encrypted, but "
            f"the passphrase the installer holds does not open it, so nothing "
            f"on this machine could ever sign with it: {opened.stderr.strip()}")



def generate_mok_keypair(target, common_name="InterGenOS Machine Owner Key",
                         passphrase=None, disk_passphrase=None):
    """Generate a fresh MOK keypair on the target system.

    Creates an RSA-2048 X.509 self-signed cert + private key under
    /var/lib/intergen/mok/ on the target. The keypair is per-install —
    different on every machine, never reused.

    The private key is ENCRYPTED with the owner's passphrase, which is
    required. There is no path through this function that produces a plain
    key, because a plain key is the finding this argument exists to close:
    every process running as root could sign a boot image the firmware
    trusts, so Secure Boot stopped an attacker without root and nobody with
    root. The generated key is read back before this function returns, and a
    key that reads back WITHOUT a passphrase raises rather than warns.

    Args:
        target: target root path
        common_name: CN field for the cert subject. Must match
            ``[A-Za-z0-9 _.-]{1,64}`` to prevent shell injection into the
            openssl ``-subj`` argument.
        passphrase: the owner's signing-key passphrase, set in the installer.
            Required. It travels to openssl through this process's own
            environment, never as an argument — an argument is readable in
            the process table by every user on the machine.
        disk_passphrase: the install's disk passphrase when there is one, so
            the check that the two differ happens here as well as in the
            frontend that collected them.

    Returns:
        dict with keys: 'key_path', 'cert_path', 'der_path'
        (all paths are inside the chroot, e.g., /var/lib/intergen/mok/mok.key)

    Raises:
        ValueError if common_name or passphrase fails its check.
        RuntimeError if keypair generation or the read-back check fails.
    """
    if not _COMMON_NAME_RE.fullmatch(common_name):
        raise ValueError(
            f"MOK common_name must match {_COMMON_NAME_RE.pattern} "
            f"(got {common_name!r})"
        )

    # Checked BEFORE the target directory is made, so a refusal leaves nothing
    # half-written for a later caller to mistake for a finished key.
    err = validate_mok_key_passphrase(passphrase, disk_passphrase)
    if err:
        raise ValueError(err)

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
    # The private key is encrypted with the owner's passphrase. The comment
    # that stood here said a passphrase "would block automated DKMS signing
    # without solving any threat we actually face". Both halves turned out to
    # be wrong, and they were measured rather than argued:
    #   - the threat is real and was the design's own. Every signing step ran
    #     unattended as root, so root could sign a boot image the firmware
    #     trusts. The chain resisted an attacker without root and no one with
    #     it, which is not what a verified boot chain is for.
    #   - nothing is blocked. Each signing tool on this system can take a
    #     passphrase without a person at a terminal: the boot-image signer
    #     reads one line from its standard input, the unified-kernel-image
    #     builder passes its own standard input through to that signer, and
    #     the module signer reads one named environment variable. Each of
    #     them refuses and writes nothing when the passphrase is absent or
    #     wrong, which is the behaviour wanted anyway.
    # Decided 2026-09-17, reversing the 2026-05 no-passphrase trade.
    #
    # -passout env:VAR: openssl reads the passphrase from this process's
    # environment. NEVER `pass:` — that puts the secret in the process table,
    # where any user on the machine can read it while openssl runs.
    # The certificate is generated with a hundred-year validity, and that is a
    # deliberate choice rather than an oversight — decided 2026-09-16 after the
    # question was measured rather than argued.
    #
    # NOTHING VERIFIES THESE DATES. The boot verifier disables the time check
    # outright and accepts an expired certificate by design, because there is no
    # trustworthy clock before the system starts; the kernel never compares a
    # certificate's window against the clock when it verifies a module
    # signature, and says so in its own source; and nothing in this project
    # checks them either. A shorter number would therefore announce a boundary
    # the machine does not enforce, and would become a boot failure the day any
    # layer began enforcing it — this key signs the boot image and the boot
    # loader on every installed machine.
    #
    # So a key is not retired by waiting for it to expire. It is retired by
    # removing it from the firmware's trusted set, which is what the offer built
    # on prior_owner_keys() below exists to do, and what the documentation and
    # the installer texts say in plain words.
    openssl_cmd = [
        "openssl", "req", "-new", "-x509",
        "-newkey", f"rsa:{MOK_KEY_BITS}",
        "-keyout", key_fspath,
        "-out", cert_fspath,
        "-outform", "PEM",
        "-days", "36500",
        "-passout", f"env:{_PASS_ENV}",
        "-subj", f"/CN={common_name}/",
    ]
    result = subprocess.run(
        openssl_cmd, capture_output=True, text=True,
        env=_env_with_passphrase(passphrase))
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

    # The install's own check, run on the bytes that were just written rather
    # than on the intent that wrote them. A key that opens with an empty
    # passphrase fails the install here; every machine installed before this
    # change shipped exactly that key and nothing noticed for four months.
    verify_key_is_encrypted(target, key_path, passphrase)

    # Recorded only after the read-back proved it, so the record cannot say
    # "protected" about a key that is not.
    record_key_protection(target, True)

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

# A world-readable record of whether the private key has a passphrase on it.
#
# The key itself is in a directory only root can open, which is correct and is
# not going to change so that a status line can be drawn. The first-login page
# runs as the person, so what it reads is this: plain text, one fact, written by
# the two places that can change that fact — here, when the key is made, and the
# signing helper on an installed machine, when it protects an older one.
#
# It is a record and it says so. A machine with no record is not a machine with
# a protected key, and the page that reads it says "not recorded" rather than
# inventing the comfortable answer.
MOK_PROTECTION_RECORD = "/etc/intergenos/mok-key-protection"


def record_key_protection(target, protected):
    """Write the world-readable record of the key's protection state.

    Returns the in-target path. The file is deliberately dull: a person can read
    it with cat and see the same fact the first-login page shows them.
    """
    import datetime
    import os
    path = Path(target) / MOK_PROTECTION_RECORD.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    path.write_text(
        "# Whether this machine's signing key is protected by a passphrase.\n"
        "# That key signs this machine's boot images and driver modules. This\n"
        "# file is a record, written when the key was made or when it was\n"
        "# protected; the key itself is readable only by root.\n"
        f"protected={'yes' if protected else 'no'}\n"
        f"recorded={stamp}\n",
        encoding="utf-8")
    os.chmod(path, 0o644)
    return MOK_PROTECTION_RECORD


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


def export_enrolled_certificates(target=None, runner=None):
    """Every certificate the firmware currently trusts as a machine owner key.

    Returns a list of DER byte strings, or None when the store cannot be read
    at all — an unreadable store is never reported as an empty one, because
    "no prior keys" and "could not look" must not reach a person as the same
    sentence.

    The trusted set belongs to the MACHINE, not to the system being installed,
    so with no target this reads it on the live system — which is what the
    installer's own screens need, since they ask the person before a target
    exists. With a target, it reads through the target's tooling with the
    firmware variables mounted, the same way the enrolment does, which is what
    the install phase uses once the system is in place.

    Either way it only writes the exported files and reads them back; it
    changes no key store.
    """
    import tempfile
    if target is None:
        import subprocess
        work = Path(tempfile.mkdtemp(prefix="igos-mok-enrolled-"))
        try:
            proc = subprocess.run(
                ["mokutil", "--export"], cwd=str(work),
                capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return _read_exported(work)

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
    return _read_exported(export_dir)


def _read_exported(directory):
    """The exported certificates in name order, or None if one cannot be read."""
    ders = []
    for path in sorted(Path(directory).glob("MOK-*.der")):
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

    `current_der` of None means THIS INSTALL HAS NOT GENERATED ITS KEY YET, and
    then every one of this project's enrolled keys is a prior one — which is the
    installer's true state at the moment the person is asked, because the new
    key is made later, in the bootloader phase. A caller on a system that does
    hold a key must pass it; passing None there would offer the machine's own
    working key for removal.
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


def retire_choice(prior_keys, chose_to_retire, password):
    """Whether this install should ask the firmware to retire prior keys.

    Three things must all hold, and each one is a rule rather than a detail:
    there is something to retire; the person said so; and an enrolment password
    exists, because the firmware asks for it to confirm a removal exactly as it
    does an addition. A person who skipped enrolment therefore keeps every old
    key — there is no path here that removes something without a person having
    answered a question with the password in hand.
    """
    return bool(prior_keys and chose_to_retire and password)


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


def sign_efi_binary(target, binary_path, key_path, cert_path, output_path=None,
                    passphrase=None):
    """Sign an EFI binary (GRUB, kernel image) with the owner's key via sbsign.

    The key is encrypted, so the signer needs the owner's passphrase. sbsign has
    no option and no environment variable for it: it loads the key through
    OpenSSL's default passphrase callback, which reads ONE LINE from standard
    input when standard input is a pipe. That is measured behaviour of the
    installed sbsign, not an assumption, and it is why this feeds exactly one
    line and why a caller signing several binaries calls this once per binary
    rather than once for the set.

    With no passphrase available the signer refuses and writes no output file at
    all — its own behaviour, which is the behaviour wanted. This function refuses
    before reaching it, so the reason is a sentence rather than a stack of
    OpenSSL errors.

    Args:
        target: target root path
        binary_path: path inside chroot to the EFI binary to sign
        key_path: path inside chroot to the signing private key (PEM)
        cert_path: path inside chroot to the signing cert (PEM)
        output_path: path inside chroot for the signed output. If None,
                     overwrites binary_path in place (sbsign --output same).
        passphrase: the owner's signing-key passphrase. Required.

    Returns:
        Path to the signed binary (always inside chroot).

    Raises:
        ValueError if no passphrase was given.
        RuntimeError if sbsign fails.
    """
    if not passphrase:
        raise ValueError(
            f"signing {binary_path} needs the machine owner's signing-key "
            f"passphrase; the key is encrypted at rest and the signer cannot "
            f"open it without one")

    if output_path is None:
        output_path = binary_path

    cmd = (
        f"sbsign --key {key_path} --cert {cert_path} "
        f"--output {output_path} {binary_path}"
    )
    rc, stdout, stderr = run_chroot_stdin(str(target), cmd, f"{passphrase}\n")
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

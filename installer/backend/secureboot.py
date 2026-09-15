# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Install-time Secure Boot state query for the Forge installer.

A lightweight, dependency-free read of the UEFI SecureBoot EFI variable,
used by the Confirm screen (D-2) to decide whether skipping MOK enrollment
needs an explicit no-boot acknowledgment.

The comprehensive POST-BOOT runtime verifier lives at
installer/tests/class2_runtime_sb_state.py (SecureBoot + SetupMode +
mokutil cross-check). This is the minimal INSTALL-TIME query, kept in the
backend so the production frontend never imports from the tests package
(which may not ship in the installed installer image). The GUID + binary
format are intentionally identical to the verifier's.

EFI variable binary format (/sys/firmware/efi/efivars/<Name>-<GUID>):
  bytes [0..3] — EFI_VARIABLE_ATTRIBUTES (uint32 LE)
  bytes [4..]  — raw payload (a single 0/1 byte for SecureBoot)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# EFI global-variable GUID for SecureBoot (UEFI spec) — matches
# installer/tests/class2_runtime_sb_state.py:EFI_GLOBAL_GUID.
_EFI_GLOBAL_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
_EFIVARS_DIR = Path("/sys/firmware/efi/efivars")
# Present iff the host booted via UEFI (regardless of efivars readability).
_EFI_SYSFS_DIR = Path("/sys/firmware/efi")
_PAYLOAD_OFFSET = 4


def _read_efi_flag(name: str, efivars_dir: Path) -> Optional[bool]:
    """Read a 1-byte boolean EFI variable under the global GUID.

    True/False on a clean read; None when the variable is absent,
    unreadable, or malformed (the same tri-state contract every public
    function in this module exposes).
    """
    path = efivars_dir / f"{name}-{_EFI_GLOBAL_GUID}"
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if len(raw) <= _PAYLOAD_OFFSET:
        return None
    return raw[_PAYLOAD_OFFSET] == 1


def is_secure_boot_enabled(
    efivars_dir: Path = _EFIVARS_DIR,
) -> Optional[bool]:
    """Return the Secure Boot enforcement state.

    Returns:
        True  — SecureBoot EFI variable present and == 1 (enforcing).
        False — present and == 0 (off).
        None  — variable absent (non-EFI / no SB), unreadable (root
                required), or malformed.

    Callers MUST treat None as "unknown", NOT as "off". D-2 raises the
    MOK-skip warning only on a known-True so an unreadable/non-EFI host
    never shows a false (and trust-eroding) Secure-Boot warning.
    """
    return _read_efi_flag("SecureBoot", efivars_dir)


def is_setup_mode(efivars_dir: Path = _EFIVARS_DIR) -> Optional[bool]:
    """Return the firmware SetupMode state (same tri-state contract).

    True  — SetupMode == 1: the firmware has no Platform Key enrolled and
            is accepting new keys (Secure Boot cannot enforce in this state).
    False — SetupMode == 0: PK enrolled, user mode (the normal OEM state).
    None  — variable absent / unreadable / malformed.

    The comprehensive post-boot verifier
    (installer/tests/class2_runtime_sb_state.py) asserts 0 as part of the
    locked-down posture; this install-time reader exists so production code
    never imports from the tests package.
    """
    return _read_efi_flag("SetupMode", efivars_dir)


def allows_mok_enrollment(
    efivars_dir: Path = _EFIVARS_DIR,
    efi_dir: Path = _EFI_SYSFS_DIR,
) -> Optional[bool]:
    """Whether this machine can take a MOK enrollment at all.

    Returns:
        False — non-EFI boot: there is no shim/MokManager path, so MOK
                enrollment is structurally impossible.
        True  — EFI boot AND the firmware exposes Secure Boot machinery
                (the SecureBoot or SetupMode EFI variable reads): the
                MokManager enrollment path exists.
        None  — EFI boot but neither variable is readable: capability
                unknown.

    Callers MUST key confident enrollment guidance on `is True` only —
    telling a user to expect MokManager on a machine that cannot run it
    erodes trust exactly like the false Secure-Boot warning the
    is_secure_boot_enabled() contract guards against. Machines whose
    firmware offers no Secure Boot support at all surface here as None
    (EFI tree present, neither variable exposed) and correctly receive
    no enrollment guidance.
    """
    if not is_efi_firmware(efi_dir):
        return False
    if _read_efi_flag("SecureBoot", efivars_dir) is not None:
        return True
    if _read_efi_flag("SetupMode", efivars_dir) is not None:
        return True
    return None


# ---------------------------------------------------------------------------
# The firmware's Secure Boot signature database (`db`): which Microsoft
# certificate authorities this machine trusts (R001.3 row 37 companion,
# decided 2026-09-05). Read-only, unprivileged (the variable is 0644 under
# efivars), openssl only for the subject line. The shipped shim carries BOTH
# Microsoft signatures (UEFI CA 2011 and UEFI CA 2023), so this is an
# advisory about the machine, never a boot decision.
# ---------------------------------------------------------------------------

# EFI_IMAGE_SECURITY_DATABASE_GUID (UEFI spec) — the `db` / `dbx` variables.
_IMAGE_SECURITY_DB_GUID = "d719b2cb-3d3a-4596-a3bc-dad00e67656f"
# EFI_CERT_X509_GUID — a signature list whose entries are DER certificates.
_EFI_CERT_X509_GUID = "a5c059a1-94e4-4aa7-87b5-ab155c2bf072"
_SIGNATURE_LIST_HEADER = 28   # GUID(16) + ListSize(4) + HeaderSize(4) + SignatureSize(4)
_SIGNATURE_OWNER = 16         # every EFI_SIGNATURE_DATA entry starts with the owner GUID

MICROSOFT_UEFI_CA_2011 = "Microsoft Corporation UEFI CA 2011"
MICROSOFT_UEFI_CA_2023 = "Microsoft UEFI CA 2023"


def iter_signature_list_certificates(raw: bytes) -> list:
    """The DER certificates inside a chain of EFI_SIGNATURE_LIST structures.

    `raw` is the variable payload (attributes already stripped). Lists of any
    other signature type (hashes, for instance) are skipped; a malformed
    list ends the walk rather than guessing at bytes.
    """
    import struct
    import uuid
    certs = []
    off = 0
    while off + _SIGNATURE_LIST_HEADER <= len(raw):
        sig_type = str(uuid.UUID(bytes_le=raw[off:off + 16]))
        list_size, header_size, sig_size = struct.unpack(
            "<III", raw[off + 16:off + _SIGNATURE_LIST_HEADER])
        if list_size < _SIGNATURE_LIST_HEADER or off + list_size > len(raw):
            break
        if sig_type == _EFI_CERT_X509_GUID and sig_size > _SIGNATURE_OWNER:
            body = raw[off + _SIGNATURE_LIST_HEADER + header_size:off + list_size]
            for i in range(0, len(body) - sig_size + 1, sig_size):
                certs.append(bytes(body[i + _SIGNATURE_OWNER:i + sig_size]))
        off += list_size
    return certs


def read_db_certificates(efivars_dir: Path = _EFIVARS_DIR) -> Optional[list]:
    """The DER certificates the firmware's `db` holds, or None when the
    variable is absent or unreadable (non-EFI, or a locked-down efivars)."""
    path = efivars_dir / f"db-{_IMAGE_SECURITY_DB_GUID}"
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if len(raw) <= _PAYLOAD_OFFSET:
        return None
    return iter_signature_list_certificates(raw[_PAYLOAD_OFFSET:])


def certificate_common_name(der: bytes) -> Optional[str]:
    """The CN of a DER certificate via openssl, or None when it cannot be read."""
    import subprocess
    try:
        proc = subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-noout", "-subject",
             "-nameopt", "RFC2253"],
            input=der, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    subject = proc.stdout.decode("utf-8", "replace").strip()
    if subject.startswith("subject="):
        subject = subject[len("subject="):].strip()
    for part in subject.split(","):
        part = part.strip()
        if part.startswith("CN="):
            return part[3:]
    return None


def microsoft_uefi_ca_state(efivars_dir: Path = _EFIVARS_DIR) -> Optional[dict]:
    """Which Microsoft UEFI certificate authorities this firmware trusts.

    Returns None when the database cannot be read (the advisory is then
    withheld, never guessed), else a dict:
        {"2011": bool, "2023": bool, "common_names": [every CN in db]}
    """
    certs = read_db_certificates(efivars_dir)
    if certs is None:
        return None
    names = [n for n in (certificate_common_name(c) for c in certs) if n]
    return {
        "2011": MICROSOFT_UEFI_CA_2011 in names,
        "2023": MICROSOFT_UEFI_CA_2023 in names,
        "common_names": names,
    }


def microsoft_ca_advisory(state: Optional[dict]) -> str:
    """One plain-language sentence for the person, from microsoft_uefi_ca_state()."""
    if state is None:
        return ""
    both = "Microsoft UEFI CA 2011 and 2023"
    if state["2011"] and state["2023"]:
        return (f"This machine's firmware trusts both Microsoft signing "
                f"authorities ({both}); the InterGenOS boot loader is signed "
                f"under both, so it starts here either way.")
    if state["2011"] and not state["2023"]:
        return ("This machine's firmware trusts the Microsoft UEFI CA 2011 but "
                "not the 2023 authority. The InterGenOS boot loader is signed "
                "under both, so it starts here; boot loaders signed only under "
                "the 2023 authority would not, until a firmware update adds it.")
    if state["2023"] and not state["2011"]:
        return ("This machine's firmware trusts the Microsoft UEFI CA 2023 but "
                "not the 2011 authority. The InterGenOS boot loader is signed "
                "under both, so it starts here.")
    return ("This machine's firmware trusts neither Microsoft UEFI signing "
            "authority (2011 or 2023), so with Secure Boot on it would not "
            "start the InterGenOS boot loader; keep Secure Boot off, or enroll "
            "the vendor keys in firmware setup.")


def is_efi_firmware(efi_dir: Path = _EFI_SYSFS_DIR) -> bool:
    """True when the host booted via UEFI (the efi sysfs tree exists).

    Lets a caller distinguish is_secure_boot_enabled()'s two None cases:
      - None + is_efi_firmware()==False  -> BIOS / non-EFI: MOK is irrelevant,
        skipping is genuinely benign.
      - None + is_efi_firmware()==True   -> EFI host but the SecureBoot var
        was unreadable (non-root caller / broken efivarfs): SB *might* be
        enforcing, so a silent benign skip could still brick. The Confirm
        screen surfaces a softer informational note in this case (D-2
        hardening, reviewed 2026-05-29) without warning the BIOS majority.
    """
    return efi_dir.exists()

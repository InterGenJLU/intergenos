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

"""Class 2: Forge Secure Boot runtime-state verification.

Post-boot verification that the signing chain *actually* enforced Secure
Boot at runtime. Class 1 proves "our signing produced verified artifacts";
Class 2 proves "UEFI + shim + GRUB enforced that chain and the running
system is in the locked-down posture we claimed."

Scope (Class 2 — "is the running system actually in secure posture?"):
  - SecureBoot EFI variable: REQUIRED — value=1 means SB enforcement is on
  - SetupMode EFI variable: REQUIRED — value=0 means PK is enrolled, user
    mode (any value=1 means firmware is unlocked and accepting new PKs,
    which defeats SB's tamper resistance)
  - mokutil --sb-state: SUPPLEMENTARY — user-space convenience check that
    should agree with the EFI variable but goes through a different code
    path (shim's MOK database rather than firmware directly)
  - UEFI boot-order (efibootmgr): DEFERRED — separate concern, lives in
    the Class 2b "did the install register an entry?" probe at
    `installer/tests/class2b_boot_order.py`
  - Kernel module sigs (MODULE_SIG): DEFERRED to Class 5

EFI variable binary format (Linux /sys/firmware/efi/efivars/<Name>-<GUID>):
  bytes [0..3]  — EFI_VARIABLE_ATTRIBUTES (uint32 little-endian)
  bytes [4..]   — raw value payload

For SecureBoot and SetupMode the payload is a single byte (0 or 1).

Root access is usually required to read efivars. The CLI and module both
return a clean "permission denied" result instead of raising, so a non-root
dev can still run this for shape-testing.

Usage:
    sudo python3 -m installer.tests.class2_runtime_sb_state [--json]
                                                            [--report-only]

Point at a mock efivars tree for testing / a mounted target:
    python3 -m installer.tests.class2_runtime_sb_state \\
        --efivars-dir /path/to/mock/efivars

Exit codes:
    0   all required runtime-state assertions pass
    1   any required assertion fails
    2   script error (efivars inaccessible, tooling missing)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional


# EFI global-variable GUID shared by SecureBoot + SetupMode (UEFI spec).
EFI_GLOBAL_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"

DEFAULT_EFIVARS_DIR = Path("/sys/firmware/efi/efivars")

# Offset where the 1-byte payload starts (after the 4-byte attribute header)
EFIVAR_PAYLOAD_OFFSET = 4


@dataclass
class ProbeResult:
    """Result of a single runtime-state probe."""
    probe: str             # "secureboot" | "setupmode" | "mokutil"
    required: bool
    expected: str          # human-readable expected value ("1" / "0" / "enabled")
    observed: Optional[str]  # None if probe could not read the source
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuntimeSBReport:
    efivars_dir: str
    results: List[ProbeResult] = field(default_factory=list)

    def all_required_pass(self) -> bool:
        return all(r.passed for r in self.results if r.required)

    def to_dict(self) -> dict:
        return {
            "efivars_dir": self.efivars_dir,
            "all_required_pass": self.all_required_pass(),
            "results": [r.to_dict() for r in self.results],
        }


def _read_efivar_byte(efivars_dir: Path, name: str) -> tuple[Optional[int], str]:
    """Read the 1-byte payload of an EFI variable.

    Returns (value_or_None, detail). value_or_None is an int 0..255 when the
    read succeeded, None when we couldn't read the variable. detail is always
    populated (either empty on success or a short reason on failure).
    """
    path = efivars_dir / f"{name}-{EFI_GLOBAL_GUID}"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, f"{path} not present"
    except PermissionError:
        return None, f"{path} unreadable (root required)"
    except OSError as exc:
        return None, f"{path} read failed: {exc}"

    if len(raw) <= EFIVAR_PAYLOAD_OFFSET:
        return None, (
            f"{path} truncated ({len(raw)} bytes, need > "
            f"{EFIVAR_PAYLOAD_OFFSET})"
        )
    return raw[EFIVAR_PAYLOAD_OFFSET], ""


def probe_secureboot(efivars_dir: Path) -> ProbeResult:
    """Assert SecureBoot EFI variable exists and equals 1."""
    value, detail = _read_efivar_byte(efivars_dir, "SecureBoot")
    if value is None:
        return ProbeResult(
            probe="secureboot", required=True, expected="1",
            observed=None, passed=False, detail=detail,
        )
    passed = value == 1
    return ProbeResult(
        probe="secureboot", required=True, expected="1",
        observed=str(value), passed=passed,
        detail="" if passed else "SecureBoot variable present but not enabled",
    )


def probe_setupmode(efivars_dir: Path) -> ProbeResult:
    """Assert SetupMode EFI variable equals 0 (PK enrolled, firmware locked).

    SetupMode=1 means the firmware is in pre-PK-enrollment mode: anyone with
    physical access can enroll new keys, which breaks SB's tamper resistance.
    Real hardware after a normal OEM boot should always read 0.
    """
    value, detail = _read_efivar_byte(efivars_dir, "SetupMode")
    if value is None:
        return ProbeResult(
            probe="setupmode", required=True, expected="0",
            observed=None, passed=False, detail=detail,
        )
    passed = value == 0
    return ProbeResult(
        probe="setupmode", required=True, expected="0",
        observed=str(value), passed=passed,
        detail="" if passed else "firmware in Setup Mode (PK not enrolled)",
    )


def probe_mokutil() -> ProbeResult:
    """Shell out to `mokutil --sb-state` as the user-space cross-check.

    Not a required probe — on a mokutil-less host it skips cleanly. When
    present and disagreeing with the EFI variable, that's actionable
    information (suggests shim/MOK DB desync from firmware).
    """
    if not shutil.which("mokutil"):
        return ProbeResult(
            probe="mokutil", required=False, expected="enabled",
            observed=None, passed=True,
            detail="mokutil not installed (skipped cross-check)",
        )
    try:
        proc = subprocess.run(
            ["mokutil", "--sb-state"],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            probe="mokutil", required=False, expected="enabled",
            observed=None, passed=False,
            detail="mokutil --sb-state timed out",
        )
    # mokutil exits 0 whether enabled or disabled; output tells us.
    text = (proc.stdout + " " + proc.stderr).strip()
    lower = text.lower()
    if "secureboot enabled" in lower:
        return ProbeResult(
            probe="mokutil", required=False, expected="enabled",
            observed="enabled", passed=True,
        )
    if "secureboot disabled" in lower:
        return ProbeResult(
            probe="mokutil", required=False, expected="enabled",
            observed="disabled", passed=False,
            detail="mokutil reports SB disabled",
        )
    return ProbeResult(
        probe="mokutil", required=False, expected="enabled",
        observed=None, passed=False,
        detail=f"mokutil output unparseable: {text[:200]}",
    )


def run(efivars_dir: Path = DEFAULT_EFIVARS_DIR) -> RuntimeSBReport:
    report = RuntimeSBReport(efivars_dir=str(efivars_dir))
    report.results.append(probe_secureboot(efivars_dir))
    report.results.append(probe_setupmode(efivars_dir))
    report.results.append(probe_mokutil())
    return report


def _render_text(report: RuntimeSBReport) -> str:
    lines = [
        "Class 2 runtime Secure Boot state",
        f"  efivars:  {report.efivars_dir}",
        "",
        f"  {'probe':<12} {'required':<9} {'expected':<9} {'observed':<9} {'result'}",
        f"  {'-' * 5:<12} {'-' * 8:<9} {'-' * 8:<9} {'-' * 8:<9} {'-' * 6}",
    ]
    for r in report.results:
        required = "yes" if r.required else "no"
        observed = r.observed if r.observed is not None else "-"
        result = "pass" if r.passed else ("FAIL" if r.required else "skip")
        lines.append(f"  {r.probe:<12} {required:<9} {r.expected:<9} "
                     f"{observed:<9} {result}")
        if r.detail:
            lines.append(f"            ↳ {r.detail}")
    lines.append("")
    lines.append(f"  overall: "
                 f"{'PASS' if report.all_required_pass() else 'FAIL'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Class 2 Forge Secure Boot runtime-state verification",
    )
    parser.add_argument(
        "--efivars-dir",
        default=str(DEFAULT_EFIVARS_DIR),
        help=f"path to efivars dir (default: {DEFAULT_EFIVARS_DIR})",
    )
    parser.add_argument("--json", action="store_true",
                        help="emit JSON report instead of human-readable text")
    parser.add_argument("--report-only", action="store_true",
                        help="exit 0 regardless of pass/fail (diagnostic sweeps)")
    args = parser.parse_args(argv)

    efivars_dir = Path(args.efivars_dir)
    report = run(efivars_dir)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_render_text(report))

    if args.report_only:
        return 0
    return 0 if report.all_required_pass() else 1


if __name__ == "__main__":
    sys.exit(main())

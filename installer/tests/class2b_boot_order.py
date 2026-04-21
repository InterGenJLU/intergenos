"""Class 2b: Forge Secure Boot UEFI boot-order verification.

Companion to Class 2 (runtime SB state). Where Class 2 proves the
running system is in locked-down SB posture, Class 2b proves that the
installer actually left behind a discoverable InterGenOS entry in the
UEFI boot manager — so the system boots InterGenOS automatically rather
than relying on the /EFI/BOOT/bootx64.efi removable-media fallback.

Scope (Class 2b — "did the install register a real Boot#### entry?"):
  - `InterGenOS`-labeled Boot#### entry: REQUIRED
  - That entry must appear in BootOrder: REQUIRED (if absent from the
    list, firmware won't consider it during normal boot)
  - BootCurrent equals the InterGenOS entry: SUPPLEMENTARY (only
    meaningful if the test is run from a booted InterGenOS target; a
    build host running the probe will have its own BootCurrent)

Why this matters: `installer/backend/bootloader.py` calls `efibootmgr
--create` during install, but that call is best-effort and soft-fails
when efivars isn't reachable (documented TODO at bootloader.py:189).
Class 2b exists specifically to catch the silent failure mode where the
installer thought it succeeded but the boot entry never got written.

Data source: `efibootmgr -v` output. Reasons for not going straight to
raw efivars: `Boot####` entries are EFI_LOAD_OPTION-formatted binary
blobs (UEFI spec §3.1.3) that efibootmgr already parses for us
correctly. Parsing EFI_LOAD_OPTION from scratch would add real code for
no extra assertion value.

Usage:
    python3 -m installer.tests.class2b_boot_order [--label InterGenOS]
                                                  [--json]
                                                  [--report-only]

Exit codes:
    0   all required assertions pass
    1   any required assertion fails
    2   script error (efibootmgr missing, UEFI not supported)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional, Tuple


# Default label matches bootloader.py's BOOTLOADER_ID constant.
DEFAULT_LABEL = "InterGenOS"


@dataclass
class BootEntry:
    id: str           # "0000" — 4-digit hex string as efibootmgr prints
    active: bool      # True when entry marked with "*" (active)
    label: str        # "InterGenOS", "Ubuntu", "UEFI Internal Disk", etc.
    device_path: str  # everything after the label on the same line

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProbeResult:
    probe: str
    required: bool
    passed: bool
    detail: str = ""
    observed: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BootOrderReport:
    label: str
    boot_order: List[str]
    boot_current: Optional[str]
    entries: List[BootEntry]
    results: List[ProbeResult] = field(default_factory=list)

    def all_required_pass(self) -> bool:
        return all(r.passed for r in self.results if r.required)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "boot_order": self.boot_order,
            "boot_current": self.boot_current,
            "entries": [e.to_dict() for e in self.entries],
            "all_required_pass": self.all_required_pass(),
            "results": [r.to_dict() for r in self.results],
        }


# efibootmgr line formats (from upstream efibootmgr 17+):
#   BootCurrent: 0001
#   BootOrder: 0001,0000,0002
#   Boot0000* InterGenOS    HD(...)/File(...)
_RE_BOOT_CURRENT = re.compile(r"^BootCurrent:\s*([0-9A-Fa-f]{4})\s*$")
_RE_BOOT_ORDER = re.compile(r"^BootOrder:\s*([0-9A-Fa-f]{4}(?:,[0-9A-Fa-f]{4})*)\s*$")
_RE_BOOT_ENTRY = re.compile(
    r"^Boot([0-9A-Fa-f]{4})(\*?)\s+(\S.*?)(?:\t|  +)(.+)$"
)


def _parse_efibootmgr(text: str) -> Tuple[List[BootEntry], List[str], Optional[str]]:
    """Parse `efibootmgr -v` stdout -> (entries, boot_order, boot_current)."""
    entries: List[BootEntry] = []
    boot_order: List[str] = []
    boot_current: Optional[str] = None

    for line in text.splitlines():
        m = _RE_BOOT_CURRENT.match(line)
        if m:
            boot_current = m.group(1).upper()
            continue

        m = _RE_BOOT_ORDER.match(line)
        if m:
            boot_order = [x.upper() for x in m.group(1).split(",")]
            continue

        m = _RE_BOOT_ENTRY.match(line)
        if m:
            entries.append(BootEntry(
                id=m.group(1).upper(),
                active=(m.group(2) == "*"),
                label=m.group(3).strip(),
                device_path=m.group(4).strip(),
            ))
            continue
        # Unknown line (headers like "Timeout:", continuation lines of
        # a previous entry that efibootmgr wraps in some formats) —
        # ignore silently. We only assert on what we can identify.

    return entries, boot_order, boot_current


def read_efibootmgr(
    efibootmgr_bin: str = "efibootmgr",
) -> Tuple[List[BootEntry], List[str], Optional[str], str]:
    """Shell out to efibootmgr and parse the output.

    Returns (entries, boot_order, boot_current, detail). On failure, the
    first three fields are empty / None and detail carries the reason.
    """
    if not shutil.which(efibootmgr_bin):
        return [], [], None, f"{efibootmgr_bin} not in PATH"
    try:
        proc = subprocess.run(
            [efibootmgr_bin, "-v"],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return [], [], None, "efibootmgr -v timed out"

    if proc.returncode != 0:
        # efibootmgr exits non-zero when efivars isn't mounted / not UEFI
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        return [], [], None, f"efibootmgr -v failed: {err}"

    entries, boot_order, boot_current = _parse_efibootmgr(proc.stdout)
    return entries, boot_order, boot_current, ""


def _find_by_label(entries: List[BootEntry], label: str) -> List[BootEntry]:
    return [e for e in entries if e.label == label]


def probe_entry_exists(entries: List[BootEntry], label: str) -> ProbeResult:
    matches = _find_by_label(entries, label)
    if not matches:
        return ProbeResult(
            probe="entry-exists", required=True, passed=False,
            detail=f"no Boot#### entry with label {label!r}",
        )
    # Multiple matches (duplicate installs) is a soft warning — pass
    # with a detail note. A stricter policy could fail here.
    ids = ",".join(m.id for m in matches)
    detail = f"multiple entries: {ids}" if len(matches) > 1 else ""
    return ProbeResult(
        probe="entry-exists", required=True, passed=True,
        observed=ids, detail=detail,
    )


def probe_entry_in_boot_order(
    entries: List[BootEntry],
    boot_order: List[str],
    label: str,
) -> ProbeResult:
    matches = _find_by_label(entries, label)
    if not matches:
        # Dependency — entry-exists would already have failed. Keep the
        # message informative without double-reporting.
        return ProbeResult(
            probe="entry-in-boot-order", required=True, passed=False,
            detail=f"label {label!r} has no entries (see entry-exists)",
        )
    if not boot_order:
        return ProbeResult(
            probe="entry-in-boot-order", required=True, passed=False,
            detail="BootOrder variable is empty or missing",
        )

    match_ids = {m.id for m in matches}
    hits = [bid for bid in boot_order if bid in match_ids]
    if not hits:
        return ProbeResult(
            probe="entry-in-boot-order", required=True, passed=False,
            observed=",".join(boot_order),
            detail=(f"entries {sorted(match_ids)} exist but none appear "
                    f"in BootOrder {boot_order}"),
        )
    # Position in BootOrder matters — earlier = higher priority. Report
    # the earliest hit's index so the operator knows if InterGenOS is
    # buried.
    earliest_idx = boot_order.index(hits[0])
    return ProbeResult(
        probe="entry-in-boot-order", required=True, passed=True,
        observed=f"{hits[0]} at position {earliest_idx}",
    )


def probe_boot_current_is_label(
    entries: List[BootEntry],
    boot_current: Optional[str],
    label: str,
) -> ProbeResult:
    """Supplementary: if BootCurrent matches the label, we booted from it."""
    if boot_current is None:
        return ProbeResult(
            probe="boot-current", required=False, passed=True,
            detail="BootCurrent not reported (skipped cross-check)",
        )
    matches = _find_by_label(entries, label)
    match_ids = {m.id for m in matches}
    if boot_current in match_ids:
        return ProbeResult(
            probe="boot-current", required=False, passed=True,
            observed=boot_current,
        )
    return ProbeResult(
        probe="boot-current", required=False, passed=False,
        observed=boot_current,
        detail=(f"BootCurrent={boot_current} but label {label!r} maps to "
                f"{sorted(match_ids) or '[]'} — running on a different "
                "boot entry"),
    )


def run(label: str = DEFAULT_LABEL) -> BootOrderReport:
    entries, boot_order, boot_current, detail = read_efibootmgr()
    report = BootOrderReport(
        label=label,
        boot_order=boot_order,
        boot_current=boot_current,
        entries=entries,
    )
    if detail:
        # efibootmgr couldn't run — record one synthetic failing probe
        # so the report shape is consistent and the reason propagates.
        report.results.append(ProbeResult(
            probe="efibootmgr-read", required=True, passed=False,
            detail=detail,
        ))
        return report

    report.results.append(probe_entry_exists(entries, label))
    report.results.append(probe_entry_in_boot_order(entries, boot_order, label))
    report.results.append(probe_boot_current_is_label(entries, boot_current, label))
    return report


def _render_text(report: BootOrderReport) -> str:
    lines = [
        "Class 2b UEFI boot-order verification",
        f"  label:         {report.label}",
        f"  BootOrder:     {','.join(report.boot_order) or '<empty>'}",
        f"  BootCurrent:   {report.boot_current or '<not reported>'}",
        f"  entries:       {len(report.entries)} total",
        "",
        f"  {'probe':<22} {'required':<9} {'result':<6} {'detail'}",
        f"  {'-' * 6:<22} {'-' * 8:<9} {'-' * 4:<6} {'-' * 6}",
    ]
    for r in report.results:
        required = "yes" if r.required else "no"
        result = "pass" if r.passed else ("FAIL" if r.required else "skip")
        detail = r.detail or (r.observed or "")
        lines.append(f"  {r.probe:<22} {required:<9} {result:<6} {detail}")
    lines.append("")
    lines.append(f"  overall: "
                 f"{'PASS' if report.all_required_pass() else 'FAIL'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Class 2b Forge SB UEFI boot-order verification",
    )
    parser.add_argument("--label", default=DEFAULT_LABEL,
                        help=f"boot-entry label to match (default {DEFAULT_LABEL})")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON report instead of human-readable text")
    parser.add_argument("--report-only", action="store_true",
                        help="exit 0 regardless of pass/fail (diagnostic sweeps)")
    args = parser.parse_args(argv)

    report = run(args.label)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_render_text(report))

    if args.report_only:
        return 0
    return 0 if report.all_required_pass() else 1


if __name__ == "__main__":
    sys.exit(main())

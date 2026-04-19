"""Class 1: Forge Secure Boot signed-chain verification.

Post-install verification that walks the boot chain and confirms each
stage that WE sign verifies against the expected MOK cert. Surfaces gaps
(missing signatures) so pre-Monday regressions are caught before hardware.

Scope (Class 1 — "did OUR signing work end-to-end?"):
  - GRUB EFI binary: REQUIRED — signed by MOK at install time
  - Kernel image(s): REQUIRED — expected to be signed by distro key or MOK
  - Shim: SKIP — signed by Fedora/MS, not us (Class 2 validates runtime SB state)
  - Kernel modules: DEFERRED to Class 5 (uses MODULE_SIG format, not PE/COFF)

FAT32 note: ESP is FAT32 and case-insensitive. bootloader.py uses
BOOTLOADER_ID='InterGenOS' (CamelCase) but ESP_BOOT_DIR is lowercase.
On a real ESP both resolve to the same directory. On a Linux filesystem
(e.g. artifact tree on ext4 before install), case matters — we probe both.

Usage:
    python -m installer.tests.class1_chain_verify [--target PATH]
                                                  [--mok-cert PATH]
                                                  [--json]
                                                  [--report-only]

Exit codes:
    0   all required artifacts present and verified
    1   any required artifact missing or fails verification
    2   script error (missing tooling, target not found)
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional

DEFAULT_MOK_CERT_RELPATH = "var/lib/intergen/mok/mok.crt"

# Chain stages. Lowercase path per ESP FAT32 norm; we also probe CamelCase
# on non-FAT32 artifact trees (build VM ext4 fixtures etc).
GRUB_RELPATHS = [
    "boot/efi/EFI/intergenos/grubx64.efi",
    "boot/efi/EFI/InterGenOS/grubx64.efi",
]
KERNEL_GLOB = "boot/vmlinuz-*"


@dataclass
class ArtifactResult:
    """Result of verifying a single artifact."""
    stage: str            # "grub" | "kernel" | "shim"
    path: str             # path probed
    required: bool        # is this a Class 1 must-have?
    sig_present: bool     # does the binary carry a signature block?
    verified: bool        # did it verify against the supplied cert?
    cert: str             # cert path used for verification
    detail: str = ""      # human-readable note (failure reason, skip reason)

    def is_pass(self) -> bool:
        if not self.required:
            return True  # skipped / optional stages never fail the run
        return self.verified


@dataclass
class ChainReport:
    """Aggregate report across all chain stages."""
    target: str
    mok_cert: str
    results: List[ArtifactResult] = field(default_factory=list)

    def all_required_pass(self) -> bool:
        return all(r.is_pass() for r in self.results)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "mok_cert": self.mok_cert,
            "all_required_pass": self.all_required_pass(),
            "results": [asdict(r) for r in self.results],
        }


def _sbverify(binary: Path, cert: Path) -> tuple[bool, bool, str]:
    """Run sbverify; return (sig_present, verified, detail).

    sbverify exits 0 on OK, nonzero on either "no sig" or "bad sig".
    Distinguish the two by parsing stderr.
    """
    try:
        proc = subprocess.run(
            ["sbverify", "--cert", str(cert), str(binary)],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return False, False, "sbverify not installed on host"
    except subprocess.TimeoutExpired:
        return False, False, "sbverify timeout (>10s)"

    if proc.returncode == 0:
        return True, True, ""

    err_text = (proc.stderr + " " + proc.stdout).lower()
    if "no signature" in err_text or "no certificate" in err_text:
        return False, False, "no signature block attached"
    return True, False, (proc.stderr.strip() or proc.stdout.strip() or
                         "signature present but verification failed")


def _probe_first_existing(target: Path, relpaths: List[str]) -> Optional[Path]:
    for rel in relpaths:
        p = target / rel
        if p.exists():
            return p
    return None


def verify_grub(target: Path, mok_cert: Path) -> ArtifactResult:
    """Verify GRUB EFI binary signed with MOK at install time."""
    grub = _probe_first_existing(target, GRUB_RELPATHS)
    if grub is None:
        return ArtifactResult(
            stage="grub",
            path=str(target / GRUB_RELPATHS[0]),
            required=True,
            sig_present=False,
            verified=False,
            cert=str(mok_cert),
            detail="grub EFI binary not found under any candidate path",
        )

    sig_present, verified, detail = _sbverify(grub, mok_cert)
    return ArtifactResult(
        stage="grub",
        path=str(grub),
        required=True,
        sig_present=sig_present,
        verified=verified,
        cert=str(mok_cert),
        detail=detail,
    )


def verify_kernels(target: Path, mok_cert: Path) -> List[ArtifactResult]:
    """Verify every vmlinuz-* under <target>/boot/.

    NOTE: as of 2026-04-19, the kernel package does not sbsign the kernel
    image. This check will surface that gap — expected to fail cleanly
    with 'no signature block attached' until the kernel signing step
    lands. Flagged to main via channel.
    """
    boot_dir = target / "boot"
    if not boot_dir.exists():
        return [ArtifactResult(
            stage="kernel",
            path=str(boot_dir),
            required=True,
            sig_present=False,
            verified=False,
            cert=str(mok_cert),
            detail="<target>/boot does not exist",
        )]

    kernels = sorted(boot_dir.glob("vmlinuz-*"))
    if not kernels:
        return [ArtifactResult(
            stage="kernel",
            path=str(boot_dir / "vmlinuz-*"),
            required=True,
            sig_present=False,
            verified=False,
            cert=str(mok_cert),
            detail="no vmlinuz-* under <target>/boot",
        )]

    results = []
    for k in kernels:
        sig_present, verified, detail = _sbverify(k, mok_cert)
        results.append(ArtifactResult(
            stage="kernel",
            path=str(k),
            required=True,
            sig_present=sig_present,
            verified=verified,
            cert=str(mok_cert),
            detail=detail,
        ))
    return results


def skip_shim(target: Path) -> ArtifactResult:
    """Shim is not signed by us — skip with a clear reason in the report."""
    shim = _probe_first_existing(target, [
        "boot/efi/EFI/intergenos/shimx64.efi",
        "boot/efi/EFI/InterGenOS/shimx64.efi",
        "boot/efi/EFI/BOOT/BOOTX64.EFI",
    ])
    return ArtifactResult(
        stage="shim",
        path=str(shim) if shim else "not found",
        required=False,
        sig_present=shim is not None,
        verified=False,
        cert="not applicable",
        detail="shim is signed by Fedora/MS, not by our MOK — Class 2 "
               "validates runtime SB state instead",
    )


def run(target: Path, mok_cert: Path) -> ChainReport:
    """Walk the chain; return a ChainReport."""
    report = ChainReport(target=str(target), mok_cert=str(mok_cert))
    report.results.append(skip_shim(target))
    report.results.append(verify_grub(target, mok_cert))
    report.results.extend(verify_kernels(target, mok_cert))
    return report


def _render_text(report: ChainReport) -> str:
    lines = [
        f"Class 1 signed-chain verification",
        f"  target:   {report.target}",
        f"  mok-cert: {report.mok_cert}",
        "",
        f"  {'stage':<8} {'result':<8} {'sig':<5} {'path'}",
        f"  {'-' * 6:<8} {'-' * 6:<8} {'-' * 3:<5} {'-' * 4}",
    ]
    for r in report.results:
        if not r.required:
            result = "skip"
        elif r.verified:
            result = "pass"
        else:
            result = "FAIL"
        sig = "yes" if r.sig_present else "no"
        lines.append(f"  {r.stage:<8} {result:<8} {sig:<5} {r.path}")
        if r.detail:
            lines.append(f"           ↳ {r.detail}")
    lines.append("")
    lines.append(
        f"  overall: {'PASS' if report.all_required_pass() else 'FAIL'}"
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Class 1 Forge Secure Boot chain verification"
    )
    parser.add_argument(
        "--target",
        default="/",
        help="root path of installed system (default: /)",
    )
    parser.add_argument(
        "--mok-cert",
        default=None,
        help=f"MOK cert path (default: <target>/{DEFAULT_MOK_CERT_RELPATH})",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="emit JSON report instead of human-readable text",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="exit 0 regardless of pass/fail (for diagnostic sweeps)",
    )
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.exists():
        print(f"error: target {target} does not exist", file=sys.stderr)
        return 2

    mok_cert = Path(args.mok_cert) if args.mok_cert else (
        target / DEFAULT_MOK_CERT_RELPATH
    )
    if not mok_cert.exists():
        print(f"error: MOK cert {mok_cert} does not exist", file=sys.stderr)
        return 2

    report = run(target, mok_cert)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_render_text(report))

    if args.report_only:
        return 0
    return 0 if report.all_required_pass() else 1


if __name__ == "__main__":
    sys.exit(main())

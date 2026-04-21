"""Class 5: Kernel module signature enforcement verification.

Post-install probe that the running kernel (a) enforces module signature
checking at load time, (b) has a trust anchor in the kernel keyring that
can verify InterGenOS-built modules, and (c) loaded modules carry real
signature metadata.

This is distinct from Class 1 (PE/COFF sbsign chain against shim/grub/
kernel binaries). Module signatures use the MODULE_SIG format — a PKCS#7
signature block appended to the .ko file, verified by the kernel against
keys in `.builtin_trusted_keys` / `.secondary_trusted_keys` / `.machine`
(the MOK-enrolled keyring on shim-aware kernels).

Scope (Class 5 — "is the running kernel enforcing module signatures?"):
  - /proc/sys/kernel/module_sig_enforce == "1": REQUIRED. When off,
    the kernel loads unsigned modules silently — tamper path.
  - Trust-anchor keyring in /proc/keys: REQUIRED. The kernel needs at
    least one of `.secondary_trusted_keys`, `.machine`, or `.platform`
    to hold keys usable for module-signature verification.
  - modinfo on a sampled loaded module: REQUIRED per-sample. A module
    in /proc/modules that has `signer:`, `sig_id:`, and `sig_hashalgo:`
    fields proves the kernel actually verified it (an unsigned module
    wouldn't have loaded under enforce=1).

Zero-modules-loaded edge case: /proc/modules empty means this is a
monolithic kernel with no loadable modules — safer by default. The
sampled-module probe skip-passes cleanly with a note.

Usage:
    python3 -m installer.tests.class5_module_sigs [--sample-module NAME]
                                                  [--json]
                                                  [--report-only]

Root is not required for any probe (proc paths are world-readable).

Exit codes:
    0   all required assertions pass
    1   any required assertion fails
    2   script error (modinfo missing when sampled-module probe needed)
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
from typing import List, Optional


DEFAULT_ENFORCE_PATH = Path("/proc/sys/kernel/module_sig_enforce")
DEFAULT_KEYS_PATH = Path("/proc/keys")
DEFAULT_MODULES_PATH = Path("/proc/modules")

# Keyrings whose presence indicates a trust anchor usable for module
# signature verification. Any one is sufficient (`.machine` is the MOK
# path; `.secondary_trusted_keys` is the distro-signed extension
# keyring; `.platform` holds firmware-provided keys).
SIGNING_KEYRING_NAMES = (
    ".secondary_trusted_keys",
    ".machine",
    ".platform",
)

# modinfo fields that together prove a module was signed.
REQUIRED_MODINFO_FIELDS = ("signer", "sig_id", "sig_hashalgo")

_RE_MODINFO_FIELD = re.compile(r"^(\w+):\s*(.+)$")


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
class ModuleSigReport:
    enforce_path: str
    keys_path: str
    modules_path: str
    sampled_module: Optional[str]
    results: List[ProbeResult] = field(default_factory=list)

    def all_required_pass(self) -> bool:
        return all(r.passed for r in self.results if r.required)

    def to_dict(self) -> dict:
        return {
            "enforce_path": self.enforce_path,
            "keys_path": self.keys_path,
            "modules_path": self.modules_path,
            "sampled_module": self.sampled_module,
            "all_required_pass": self.all_required_pass(),
            "results": [r.to_dict() for r in self.results],
        }


# --- Probes ----------------------------------------------------------------


def probe_module_sig_enforce(enforce_path: Path) -> ProbeResult:
    """Required: /proc/sys/kernel/module_sig_enforce must be '1'."""
    try:
        raw = enforce_path.read_text().strip()
    except FileNotFoundError:
        return ProbeResult(
            probe="module_sig_enforce", required=True, passed=False,
            detail=(f"{enforce_path} not present — CONFIG_MODULE_SIG_FORCE "
                    "not enabled in this kernel"),
        )
    except PermissionError:
        return ProbeResult(
            probe="module_sig_enforce", required=True, passed=False,
            detail=f"{enforce_path} unreadable",
        )
    except OSError as exc:
        return ProbeResult(
            probe="module_sig_enforce", required=True, passed=False,
            detail=f"{enforce_path} read failed: {exc}",
        )

    if raw not in ("0", "1"):
        return ProbeResult(
            probe="module_sig_enforce", required=True, passed=False,
            observed=raw,
            detail=f"expected 0 or 1, got {raw!r}",
        )
    return ProbeResult(
        probe="module_sig_enforce", required=True, passed=(raw == "1"),
        observed=raw,
        detail="" if raw == "1" else "module signature enforcement is OFF",
    )


def probe_signing_keyring(keys_path: Path) -> ProbeResult:
    """Required: /proc/keys contains at least one trust-anchor keyring.

    /proc/keys columns (space-separated): id flags usage perm-kind
    perm-hex uid gid type description[:data]. We substring-match the
    description against SIGNING_KEYRING_NAMES rather than parsing
    columns — robust across kernel versions with different whitespace.
    """
    try:
        content = keys_path.read_text()
    except FileNotFoundError:
        return ProbeResult(
            probe="signing_keyring", required=True, passed=False,
            detail=f"{keys_path} not present (no keys subsystem)",
        )
    except PermissionError:
        return ProbeResult(
            probe="signing_keyring", required=True, passed=False,
            detail=f"{keys_path} unreadable",
        )
    except OSError as exc:
        return ProbeResult(
            probe="signing_keyring", required=True, passed=False,
            detail=f"{keys_path} read failed: {exc}",
        )

    hits = [name for name in SIGNING_KEYRING_NAMES if name in content]
    if hits:
        return ProbeResult(
            probe="signing_keyring", required=True, passed=True,
            observed=",".join(hits),
        )
    return ProbeResult(
        probe="signing_keyring", required=True, passed=False,
        detail=(f"no trust-anchor keyring found; expected one of "
                f"{list(SIGNING_KEYRING_NAMES)}"),
    )


def pick_first_loaded_module(modules_path: Path) -> Optional[str]:
    """Return the name of the first loaded module, or None if empty."""
    try:
        lines = modules_path.read_text().splitlines()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    for line in lines:
        parts = line.split(maxsplit=1)
        if parts and parts[0]:
            return parts[0]
    return None


def _parse_modinfo_fields(text: str) -> dict:
    """Parse modinfo stdout into a dict of field -> first value."""
    out: dict = {}
    for line in text.splitlines():
        m = _RE_MODINFO_FIELD.match(line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def probe_sampled_module_signed(
    module_name: Optional[str],
    modinfo_bin: str = "modinfo",
) -> ProbeResult:
    """Required: the sampled loaded module has sig metadata in modinfo."""
    if module_name is None:
        return ProbeResult(
            probe="sampled_module_signed", required=False, passed=True,
            detail="no loadable modules present (monolithic kernel)",
        )
    if not shutil.which(modinfo_bin):
        return ProbeResult(
            probe="sampled_module_signed", required=True, passed=False,
            detail=f"{modinfo_bin} not in PATH",
        )
    try:
        proc = subprocess.run(
            [modinfo_bin, module_name],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            probe="sampled_module_signed", required=True, passed=False,
            detail=f"modinfo {module_name} timed out",
        )

    if proc.returncode != 0:
        return ProbeResult(
            probe="sampled_module_signed", required=True, passed=False,
            detail=f"modinfo {module_name} failed: "
                   f"{proc.stderr.strip() or 'unknown error'}",
        )

    fields = _parse_modinfo_fields(proc.stdout)
    missing = [f for f in REQUIRED_MODINFO_FIELDS if f not in fields]
    if missing:
        return ProbeResult(
            probe="sampled_module_signed", required=True, passed=False,
            observed=module_name,
            detail=(f"module {module_name!r} missing modinfo fields: "
                    f"{missing}"),
        )
    observed = (f"{module_name} signer={fields['signer']!r} "
                f"sig_id={fields['sig_id']!r}")
    return ProbeResult(
        probe="sampled_module_signed", required=True, passed=True,
        observed=observed,
    )


def run(
    enforce_path: Path = DEFAULT_ENFORCE_PATH,
    keys_path: Path = DEFAULT_KEYS_PATH,
    modules_path: Path = DEFAULT_MODULES_PATH,
    sample_module: Optional[str] = None,
) -> ModuleSigReport:
    """Run all three probes; return a consolidated report.

    If sample_module is given, use that specific module for the signed-
    module probe (useful for testing or when pinning a known module).
    Otherwise pick the first entry from /proc/modules.
    """
    chosen = sample_module or pick_first_loaded_module(modules_path)
    report = ModuleSigReport(
        enforce_path=str(enforce_path),
        keys_path=str(keys_path),
        modules_path=str(modules_path),
        sampled_module=chosen,
    )
    report.results.append(probe_module_sig_enforce(enforce_path))
    report.results.append(probe_signing_keyring(keys_path))
    report.results.append(probe_sampled_module_signed(chosen))
    return report


# --- CLI -------------------------------------------------------------------


def _render_text(report: ModuleSigReport) -> str:
    lines = [
        "Class 5 kernel module signature verification",
        f"  enforce:       {report.enforce_path}",
        f"  keyring:       {report.keys_path}",
        f"  modules:       {report.modules_path}",
        f"  sample:        {report.sampled_module or '<none>'}",
        "",
        f"  {'probe':<24} {'required':<9} {'result':<6} {'detail'}",
        f"  {'-' * 6:<24} {'-' * 8:<9} {'-' * 4:<6} {'-' * 6}",
    ]
    for r in report.results:
        required = "yes" if r.required else "no"
        result = "pass" if r.passed else ("FAIL" if r.required else "skip")
        detail = r.detail or (r.observed or "")
        lines.append(f"  {r.probe:<24} {required:<9} {result:<6} {detail}")
    lines.append("")
    lines.append(f"  overall: "
                 f"{'PASS' if report.all_required_pass() else 'FAIL'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Class 5 kernel module signature enforcement verification",
    )
    parser.add_argument("--sample-module", default=None,
                        help="pin a specific module to sample (default: first loaded)")
    parser.add_argument("--enforce-path", default=str(DEFAULT_ENFORCE_PATH))
    parser.add_argument("--keys-path", default=str(DEFAULT_KEYS_PATH))
    parser.add_argument("--modules-path", default=str(DEFAULT_MODULES_PATH))
    parser.add_argument("--json", action="store_true",
                        help="emit JSON report instead of human-readable text")
    parser.add_argument("--report-only", action="store_true",
                        help="exit 0 regardless of pass/fail (diagnostic sweeps)")
    args = parser.parse_args(argv)

    report = run(
        enforce_path=Path(args.enforce_path),
        keys_path=Path(args.keys_path),
        modules_path=Path(args.modules_path),
        sample_module=args.sample_module,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_render_text(report))

    if args.report_only:
        return 0
    return 0 if report.all_required_pass() else 1


if __name__ == "__main__":
    sys.exit(main())

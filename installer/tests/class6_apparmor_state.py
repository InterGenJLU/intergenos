"""Class 6: AppArmor MAC enforcement state verification.

Post-install probe that the running kernel (a) has the AppArmor LSM
loaded and active, (b) has the apparmor.service systemd unit running so
profiles are kept in sync with /etc/apparmor.d/, and (c) has at least
one profile loaded — with a sampled-profile probe confirming the
profile is in `enforce` or `complain` mode (either is reviewer-defensible
for v1.0; the vote dispatch ratified `complain` as the default rollout
mode and graduation to `enforce` per-profile as confidence builds).

This class is distinct from the other Forge SB classes because it sits
above the boot-chain integrity layer that Classes 1/2/2b/5 protect —
AppArmor activates at userspace init time, after the entire signed-chain
+ lockdown=integrity chain has already handed off. Boot-chain failures
surface in Classes 1-5; AppArmor failures surface here.

Scope (Class 6 — "is the running userspace MAC enforcement live?"):
  - /sys/module/apparmor/parameters/enabled == "Y": REQUIRED. The kernel
    has CONFIG_SECURITY_APPARMOR=y AND apparmor=1 boot parameter (or
    no apparmor=0 override). Without "Y" the LSM is compiled out or
    runtime-disabled — profiles cannot enforce.
  - apparmor.service is active: REQUIRED. The systemd unit reads
    /etc/apparmor.d/ and pushes profiles into the kernel. Without the
    service running, profiles drift between disk and kernel.
  - /sys/kernel/security/apparmor/profiles has ≥1 entry: REQUIRED. A
    loaded LSM with zero profiles is a no-op.
  - Sampled profile is in enforce or complain mode: REQUIRED per-sample.
    A profile in /etc/apparmor.d/ that's not loaded would fail this; a
    loaded profile in `unconfined` state would also fail.

Zero-profiles-loaded vs one-or-more: zero is a hard fail (no MAC).
Non-zero is the threshold; v1.0 ships with `complain` as default per
design decision 4-0 unanimous A on 2026-04-29.

Usage:
    python3 -m installer.tests.class6_apparmor_state [--sample-profile NAME]
                                                     [--json]
                                                     [--report-only]

Root is not required for the kernel-state probes (sysfs paths are
world-readable on standard kernels). The systemctl probe uses the
non-root `is-active` query — also world-readable.

Exit codes:
    0   all required assertions pass
    1   any required assertion fails
    2   script error (systemctl missing when service probe needed)
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


DEFAULT_ENABLED_PATH = Path("/sys/module/apparmor/parameters/enabled")
DEFAULT_PROFILES_PATH = Path("/sys/kernel/security/apparmor/profiles")
DEFAULT_SERVICE_NAME = "apparmor.service"

# Profile modes considered "live" for v1.0.
# `complain` is the v1.0 default per design decision 2026-04-29 (logging-only
# enforcement during rollout). `enforce` graduates per-profile as
# confidence builds. `kill` is rare and acceptable. `unconfined` is a
# no-op and FAILS this probe — a profile that's loaded-but-unconfined
# is a posture defect.
LIVE_PROFILE_MODES = ("enforce", "complain", "kill")
INERT_PROFILE_MODE = "unconfined"


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
class AppArmorReport:
    enabled_path: str
    profiles_path: str
    service_name: str
    sampled_profile: Optional[str]
    results: List[ProbeResult] = field(default_factory=list)

    def all_required_pass(self) -> bool:
        return all(r.passed for r in self.results if r.required)

    def to_dict(self) -> dict:
        return {
            "enabled_path": self.enabled_path,
            "profiles_path": self.profiles_path,
            "service_name": self.service_name,
            "sampled_profile": self.sampled_profile,
            "all_required_pass": self.all_required_pass(),
            "results": [r.to_dict() for r in self.results],
        }


# --- Probes ----------------------------------------------------------------


def probe_apparmor_module_enabled(enabled_path: Path) -> ProbeResult:
    """Required: /sys/module/apparmor/parameters/enabled must be 'Y'."""
    try:
        raw = enabled_path.read_text().strip()
    except FileNotFoundError:
        return ProbeResult(
            probe="apparmor_enabled", required=True, passed=False,
            detail=(f"{enabled_path} not present — CONFIG_SECURITY_APPARMOR "
                    "not compiled in OR LSM disabled at boot"),
        )
    except PermissionError:
        return ProbeResult(
            probe="apparmor_enabled", required=True, passed=False,
            detail=f"{enabled_path} unreadable",
        )
    except OSError as exc:
        return ProbeResult(
            probe="apparmor_enabled", required=True, passed=False,
            detail=f"{enabled_path} read failed: {exc}",
        )

    if raw not in ("Y", "N"):
        return ProbeResult(
            probe="apparmor_enabled", required=True, passed=False,
            observed=raw,
            detail=f"expected Y or N, got {raw!r}",
        )
    return ProbeResult(
        probe="apparmor_enabled", required=True, passed=(raw == "Y"),
        observed=raw,
        detail="" if raw == "Y" else "AppArmor LSM is disabled at runtime",
    )


def probe_apparmor_service_active(
    service_name: str = DEFAULT_SERVICE_NAME,
    systemctl_bin: str = "systemctl",
) -> ProbeResult:
    """Required: apparmor.service is in 'active' state."""
    if not shutil.which(systemctl_bin):
        return ProbeResult(
            probe="apparmor_service_active", required=True, passed=False,
            detail=f"{systemctl_bin} not in PATH",
        )
    try:
        proc = subprocess.run(
            [systemctl_bin, "is-active", service_name],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            probe="apparmor_service_active", required=True, passed=False,
            detail=f"systemctl is-active {service_name} timed out",
        )

    state = proc.stdout.strip()
    # `is-active` exits 0 only when state == "active"; any other state
    # exits non-zero (inactive=3, failed=3, activating=0 in newer
    # systemd but state still readable from stdout).
    if state == "active":
        return ProbeResult(
            probe="apparmor_service_active", required=True, passed=True,
            observed=state,
        )
    return ProbeResult(
        probe="apparmor_service_active", required=True, passed=False,
        observed=state or "<no-output>",
        detail=f"{service_name} state is {state!r} (expected 'active')",
    )


def _parse_profiles_file(text: str) -> List[tuple]:
    """Parse /sys/kernel/security/apparmor/profiles into (name, mode).

    Format per line: "<profile-name> (<mode>)"
    Example: "/usr/bin/firefox (enforce)"
             "snap.lxd.activate (complain)"
    Anything that doesn't match the trailing-paren pattern is skipped.
    """
    out: List[tuple] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.endswith(")"):
            continue
        idx = line.rfind("(")
        if idx <= 0:
            continue
        name = line[:idx].rstrip()
        mode = line[idx + 1: -1].strip()
        if name and mode:
            out.append((name, mode))
    return out


def probe_profiles_loaded(
    profiles_path: Path,
    minimum: int = 1,
) -> ProbeResult:
    """Required: at least `minimum` profiles loaded into the kernel."""
    try:
        content = profiles_path.read_text()
    except FileNotFoundError:
        return ProbeResult(
            probe="profiles_loaded", required=True, passed=False,
            detail=(f"{profiles_path} not present — securityfs not mounted "
                    "OR AppArmor LSM not loaded"),
        )
    except PermissionError:
        return ProbeResult(
            probe="profiles_loaded", required=True, passed=False,
            detail=f"{profiles_path} unreadable",
        )
    except OSError as exc:
        return ProbeResult(
            probe="profiles_loaded", required=True, passed=False,
            detail=f"{profiles_path} read failed: {exc}",
        )

    profiles = _parse_profiles_file(content)
    count = len(profiles)
    if count >= minimum:
        return ProbeResult(
            probe="profiles_loaded", required=True, passed=True,
            observed=str(count),
            detail=f"{count} profile(s) loaded",
        )
    return ProbeResult(
        probe="profiles_loaded", required=True, passed=False,
        observed=str(count),
        detail=f"only {count} profile(s) loaded; expected ≥ {minimum}",
    )


def pick_first_loaded_profile(profiles_path: Path) -> Optional[str]:
    """Return the name of the first loaded profile, or None if empty."""
    try:
        content = profiles_path.read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    profiles = _parse_profiles_file(content)
    if not profiles:
        return None
    return profiles[0][0]


def probe_sampled_profile_mode(
    profile_name: Optional[str],
    profiles_path: Path,
) -> ProbeResult:
    """Required: sampled profile is in a live mode (enforce/complain/kill).

    A profile in `unconfined` is a no-op — fails. Profile not found in
    the loaded set is also a fail (caller selected a name that isn't in
    the kernel's view).
    """
    if profile_name is None:
        return ProbeResult(
            probe="sampled_profile_mode", required=False, passed=True,
            detail="no profiles loaded — sampled-profile probe skipped",
        )
    try:
        content = profiles_path.read_text()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return ProbeResult(
            probe="sampled_profile_mode", required=True, passed=False,
            observed=profile_name,
            detail=f"could not read {profiles_path}: {exc}",
        )

    profiles = dict(_parse_profiles_file(content))
    if profile_name not in profiles:
        return ProbeResult(
            probe="sampled_profile_mode", required=True, passed=False,
            observed=profile_name,
            detail=f"profile {profile_name!r} not in loaded set",
        )
    mode = profiles[profile_name]
    if mode in LIVE_PROFILE_MODES:
        return ProbeResult(
            probe="sampled_profile_mode", required=True, passed=True,
            observed=f"{profile_name} ({mode})",
        )
    if mode == INERT_PROFILE_MODE:
        return ProbeResult(
            probe="sampled_profile_mode", required=True, passed=False,
            observed=f"{profile_name} ({mode})",
            detail=(f"profile {profile_name!r} is unconfined — "
                    "loaded but not enforcing"),
        )
    return ProbeResult(
        probe="sampled_profile_mode", required=True, passed=False,
        observed=f"{profile_name} ({mode})",
        detail=(f"profile {profile_name!r} mode {mode!r} is not in "
                f"{list(LIVE_PROFILE_MODES)}"),
    )


def run(
    enabled_path: Path = DEFAULT_ENABLED_PATH,
    profiles_path: Path = DEFAULT_PROFILES_PATH,
    service_name: str = DEFAULT_SERVICE_NAME,
    sample_profile: Optional[str] = None,
    minimum_profiles: int = 1,
) -> AppArmorReport:
    """Run all four probes; return a consolidated report.

    If sample_profile is given, use that specific name for the sampled-
    profile mode probe. Otherwise pick the first entry from the kernel's
    profiles file.
    """
    chosen = sample_profile or pick_first_loaded_profile(profiles_path)
    report = AppArmorReport(
        enabled_path=str(enabled_path),
        profiles_path=str(profiles_path),
        service_name=service_name,
        sampled_profile=chosen,
    )
    report.results.append(probe_apparmor_module_enabled(enabled_path))
    report.results.append(probe_apparmor_service_active(service_name))
    report.results.append(probe_profiles_loaded(profiles_path,
                                                minimum=minimum_profiles))
    report.results.append(probe_sampled_profile_mode(chosen, profiles_path))
    return report


# --- CLI -------------------------------------------------------------------


def _render_text(report: AppArmorReport) -> str:
    lines = [
        "Class 6 AppArmor MAC enforcement state",
        f"  enabled:       {report.enabled_path}",
        f"  profiles:      {report.profiles_path}",
        f"  service:       {report.service_name}",
        f"  sample:        {report.sampled_profile or '<none>'}",
        "",
        f"  {'probe':<28} {'required':<9} {'result':<6} {'detail'}",
        f"  {'-' * 6:<28} {'-' * 8:<9} {'-' * 4:<6} {'-' * 6}",
    ]
    for r in report.results:
        required = "yes" if r.required else "no"
        result = "pass" if r.passed else ("FAIL" if r.required else "skip")
        detail = r.detail or (r.observed or "")
        lines.append(f"  {r.probe:<28} {required:<9} {result:<6} {detail}")
    lines.append("")
    lines.append(f"  overall: "
                 f"{'PASS' if report.all_required_pass() else 'FAIL'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Class 6 AppArmor MAC enforcement state verification",
    )
    parser.add_argument("--sample-profile", default=None,
                        help="pin a specific profile to sample (default: first loaded)")
    parser.add_argument("--enabled-path", default=str(DEFAULT_ENABLED_PATH))
    parser.add_argument("--profiles-path", default=str(DEFAULT_PROFILES_PATH))
    parser.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    parser.add_argument("--minimum-profiles", type=int, default=1,
                        help="minimum loaded-profile count required (default: 1)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON report instead of human-readable text")
    parser.add_argument("--report-only", action="store_true",
                        help="exit 0 regardless of pass/fail (diagnostic sweeps)")
    args = parser.parse_args(argv)

    report = run(
        enabled_path=Path(args.enabled_path),
        profiles_path=Path(args.profiles_path),
        service_name=args.service_name,
        sample_profile=args.sample_profile,
        minimum_profiles=args.minimum_profiles,
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

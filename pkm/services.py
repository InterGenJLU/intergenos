"""pkm services — Q5 service-restart manifest scan + restart helpers.

Per the operator-greenlit Q5 design: upgrades that ship updated daemon
binaries should NOT auto-restart the running services (PRIME DIRECTIVE
— user controls when their machine takes the downtime). Instead, pkm
scans the installed-file manifest for systemd / SysV service unit paths,
cross-references against currently-active services, and prints an
end-of-upgrade summary listing what needs user-driven restart. For
kernel / glibc / systemd-itself / initramfs packages, the message
escalates to REBOOT REQUIRED because no userspace restart can fix the
live-vs-on-disk divergence.

This module owns the implementation primitives:

  - scan_manifest_for_services(file_list) → list of unit paths the
    package installed (systemd .service files + /etc/init.d/* scripts)
  - query_active_services(unit_names) → subset of unit_names that are
    currently active under systemctl
  - classify_restart_requirement(package_name, file_list) → dict
    describing what the user should do (reboot, restart, none)
  - format_service_summary(classification) → multi-line summary for
    end-of-upgrade output
  - run_restart_services(unit_names) → dict {unit: success_bool}

The CLI subcommand pkm restart-services (which calls run_restart_services)
lives in pkm/cli.py and is wired separately by the upgrade orchestration.

Reboot-required packages are the packages whose deploy fundamentally
diverges userspace from on-disk in ways no service restart can resolve:
kernel image (running kernel still loaded in memory), glibc (loaded by
every running process), systemd itself (PID 1 cannot exec a new binary
in-place), and initramfs-related packages (changes only take effect on
next boot's early boot path).
"""

import re
import subprocess
from pathlib import Path


# Packages whose upgrade always requires a reboot to take effect on the
# live system. Userspace restart cannot resolve the on-disk-vs-in-memory
# divergence for these. Names match the package-name field in the
# installed table.
REBOOT_TRIGGER_PACKAGES = frozenset({
    "linux-kernel",      # running kernel image stays loaded until reboot
    "linux-firmware",    # firmware blobs loaded at boot
    "glibc", "glibc-core",  # loaded by every running process; cannot live-swap
    "systemd",           # PID 1 cannot in-place exec
    "intel-ucode", "amd-ucode",  # microcode applied at early boot
    "shim", "shim-signed",  # bootloader; relevant on next firmware boot
    "grub", "grub2",     # bootloader; relevant on next firmware boot
})


# Path patterns the manifest may contain for service-unit files.
_SYSTEMD_UNIT_RE = re.compile(
    r"^(usr/lib|etc)/systemd/system/([^/]+\.service)$"
)
_SYSVINIT_RE = re.compile(r"^etc/init\.d/([^/]+)$")


def scan_manifest_for_services(file_list):
    """Return list of service unit names installed by a package.

    Args:
        file_list: list of relative paths (no leading slash; dirs end in "/")
            matching installer.py's manifest shape.

    Returns:
        list[str] — unit names (e.g. "postgresql.service", "nginx").
        Systemd unit basenames are returned with their .service suffix;
        SysV init scripts are returned without prefix. Order matches
        appearance in file_list.
    """
    units = []
    for p in file_list:
        if p.endswith("/"):
            continue
        m = _SYSTEMD_UNIT_RE.match(p)
        if m:
            units.append(m.group(2))
            continue
        m = _SYSVINIT_RE.match(p)
        if m:
            units.append(m.group(1))
    return units


def query_active_services(unit_names):
    """Return the subset of unit_names that systemd currently reports active.

    Runs `systemctl is-active <unit>` per unit; on systems without systemd
    (chroot, container, non-systemd init) returns empty list silently —
    nothing to restart if nothing is running.

    Args:
        unit_names: list of unit names (with or without .service suffix).

    Returns:
        list[str] — unit names that are currently active. Each returned
        name matches its input form (no normalization).
    """
    if not unit_names:
        return []
    active = []
    for unit in unit_names:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", unit],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                active.append(unit)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # No systemd available — silent. Nothing to flag for restart.
            return []
    return active


def classify_restart_requirement(package_name, file_list):
    """Classify what the user must do after upgrading this package.

    Args:
        package_name: name of the package being upgraded.
        file_list: list of relative paths the package ships.

    Returns:
        dict with three fields:
          requirement: "reboot" | "restart" | "none"
          services: list[str] of running unit names needing restart
            (empty when requirement != "restart")
          reason: short human-readable string explaining the verdict
    """
    if package_name in REBOOT_TRIGGER_PACKAGES:
        return {
            "requirement": "reboot",
            "services": [],
            "reason": (
                f"{package_name} change takes effect only on next boot "
                f"(running kernel/libc/init cannot be live-replaced)"
            ),
        }
    units = scan_manifest_for_services(file_list)
    if not units:
        return {
            "requirement": "none",
            "services": [],
            "reason": "no service units in package manifest",
        }
    active = query_active_services(units)
    if not active:
        return {
            "requirement": "none",
            "services": [],
            "reason": (
                "service units present in package but none are currently active"
            ),
        }
    return {
        "requirement": "restart",
        "services": active,
        "reason": (
            f"{len(active)} running service(s) upgraded — restart to load new code"
        ),
    }


def format_service_summary(classification):
    """Render a multi-line summary suitable for end-of-upgrade output.

    Returns empty string when classification.requirement == "none" (no
    user action needed; nothing to print).
    """
    req = classification["requirement"]
    if req == "none":
        return ""
    lines = []
    if req == "reboot":
        lines.append(f"  REBOOT REQUIRED — {classification['reason']}")
        lines.append("  Run: sudo reboot")
        return "\n".join(lines)
    # restart
    services = classification["services"]
    lines.append(
        f"  The following running service(s) were upgraded and need restart: "
        f"{', '.join(services)}"
    )
    lines.append(f"  To restart all: pkm restart-services --all")
    lines.append(f"  To restart selectively: systemctl restart <name>")
    return "\n".join(lines)


def run_restart_services(unit_names):
    """Invoke `systemctl restart <unit>` for each unit name.

    Args:
        unit_names: list of unit names to restart.

    Returns:
        dict[str, bool] — {unit_name: success_bool}. Per-unit failures
        do not abort the loop; the dict captures the full outcome so the
        caller can render a partial-success summary.
    """
    results = {}
    for unit in unit_names:
        try:
            result = subprocess.run(
                ["systemctl", "restart", unit],
                capture_output=True, text=True, timeout=60,
            )
            results[unit] = result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            results[unit] = False
    return results

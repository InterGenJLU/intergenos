# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm services — Q5 service-restart manifest scan + restart helpers.

Per the approved Q5 design: upgrades that ship updated daemon
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
    describing what the user should do (reboot, restart, relogin, none)
  - format_service_summary(classification) → multi-line summary for
    end-of-upgrade output
  - format_next_steps(classifications) → ONE consolidated, strongest-first
    end-of-transaction "Next steps" block across a whole install/upgrade
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
import sys
from pathlib import Path

# Forensic-trace shim — defensive import.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


# Packages whose upgrade always requires a reboot to take effect on the
# live system. Userspace restart cannot resolve the on-disk-vs-in-memory
# divergence for these. Names match the package-name field in the
# installed table.
REBOOT_TRIGGER_PACKAGES = frozenset({
    "linux-kernel",      # running kernel image stays loaded until reboot
    "linux-firmware",    # firmware blobs loaded at boot
    "glibc", "glibc-core",  # loaded by every running process; cannot live-swap
    "systemd",           # PID 1 cannot in-place exec
    "systemd-pass2",     # the second-pass systemd build IS systemd (PID 1) —
                         # same in-place-exec constraint as `systemd` above
    "intel-ucode", "amd-ucode",  # microcode applied at early boot
    "shim", "shim-signed",  # bootloader; relevant on next firmware boot
    "grub", "grub2",     # bootloader; relevant on next firmware boot
    "gnome-shell",       # the login greeter's compiled gresource is read at
                         # display-manager start; a running session keeps the
                         # old shell/greeter until the machine reboots
})


# Path patterns the manifest may contain for service-unit files.
_SYSTEMD_UNIT_RE = re.compile(
    r"^(usr/lib|etc)/systemd/system/([^/]+\.service)$"
)
_SYSVINIT_RE = re.compile(r"^etc/init\.d/([^/]+)$")

# Boot / kernel artifacts whose upgrade takes effect only on the next boot —
# the manifest-inference fallback for reboot when a package is not in the
# structural REBOOT_TRIGGER_PACKAGES name set (e.g. an out-of-tree module pkg
# that did not declare reboot_required).
_BOOT_ARTIFACT_RE = re.compile(
    r"^boot/"                      # kernel image / initramfs / bootloader files
    r"|^usr/lib/modules/[^/]+/"    # kernel modules for a specific kernel version
    r"|(^|/)vmlinuz"
    r"|(^|/)initramfs"
)

# Desktop-shell payloads the running GNOME session compiles/loads once at
# session start: a new shell EXTENSION, icon THEME, or GTK/shell THEME is only
# picked up after logging out and back in. Anchored to theme roots (an
# index.theme marks a real theme dir) so an ordinary app shipping a stray
# hicolor icon under usr/share/icons does NOT get mis-flagged as relogin.
_RELOGIN_RE = re.compile(
    r"^usr/share/gnome-shell/extensions/[^/]+/"   # a shell extension
    r"|^usr/share/icons/[^/]+/index\.theme$"      # an icon theme (not stray app icons)
    r"|^usr/share/themes/[^/]+/index\.theme$"     # a GTK / shell theme
)


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
            if _TRACE_AVAILABLE:
                result = _trace.traced_run(
                    ["systemctl", "is-active", "--quiet", unit],
                    timeout=10, phase="pkm_service_query",
                    intent=f"is-active check for {unit}",
                )
            else:
                result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                    ["systemctl", "is-active", "--quiet", unit],
                    capture_output=True, timeout=10,
                )
            if result.returncode == 0:
                active.append(unit)
        except FileNotFoundError:
            # systemctl genuinely absent (chroot / container / non-systemd
            # init) — nothing is running to flag for restart. This is the ONLY
            # condition that justifies abandoning the whole scan.
            return []
        except (subprocess.TimeoutExpired, OSError) as e:
            # PKM-A12: a single flaky/slow unit must NOT mask every other
            # active service. Returning [] here told the user "no restart
            # needed" while running daemons had been upgraded (stale daemon
            # code). Skip this one unit (state unknown) and keep scanning.
            sys.stderr.write(
                f"  WARNING: could not query service state for {unit}: {e}; "
                f"skipping it (it may still need a restart).\n"
            )
            continue
    return active


def classify_restart_requirement(package_name, file_list,
                                 declared_reboot_required=False):
    """Classify what the user must do after installing/upgrading this package.

    Args:
        package_name: name of the package being installed/upgraded.
        file_list: list of relative paths the package ships.
        declared_reboot_required: the package's OWN declaration (3.0-F28), read
            from the installed row's reboot_required column (sourced from the
            archive .PKGINFO `reboot_required=true`). This is the authoritative,
            per-package signal: an out-of-tree kernel module gated behind a
            blacklist (nvidia's .ko behind nouveau) activates only on reboot,
            and no hardcoded name-list can know that from the package name alone.
            REBOOT_TRIGGER_PACKAGES remains as a structural fallback for the OS
            core (kernel/glibc/systemd/microcode/bootloader), which is
            reboot-critical by nature rather than by shipped payload.

    Returns:
        dict with three fields:
          requirement: "reboot" | "restart" | "relogin" | "none"
          services: list[str] of running unit names needing restart
            (empty when requirement != "restart")
          reason: short human-readable string explaining the verdict

    Precedence, strongest first: reboot (forced-loud OR boot-path manifest
    artifact) > restart (a running service unit was upgraded) > relogin (a
    desktop-shell payload the session loads only at login) > none.
    """
    if declared_reboot_required or package_name in REBOOT_TRIGGER_PACKAGES:
        return {
            "requirement": "reboot",
            "services": [],
            "reason": (
                f"{package_name} ships a payload that activates only on the "
                f"next boot (kernel module behind a blacklist, kernel image, "
                f"or other boot-path component cannot be live-loaded)"
            ),
        }
    # Manifest-inference reboot fallback: a package NOT in the structural
    # REBOOT_TRIGGER_PACKAGES set (and not self-declaring reboot_required) may
    # still ship a boot-path payload — an out-of-tree kernel module, a new
    # vmlinuz, or an initramfs component — that only takes effect on next boot.
    if any(_BOOT_ARTIFACT_RE.search(p) for p in file_list):
        return {
            "requirement": "reboot",
            "services": [],
            "reason": (
                f"{package_name} ships a boot-path artifact (kernel image, "
                f"kernel module, or initramfs component) that takes effect "
                f"only on the next boot"
            ),
        }
    units = scan_manifest_for_services(file_list)
    active = query_active_services(units) if units else []
    if active:
        return {
            "requirement": "restart",
            "services": active,
            "reason": (
                f"{len(active)} running service(s) upgraded — restart to load new code"
            ),
        }
    # No running service to restart. A desktop-shell payload (a GNOME shell
    # extension, an icon theme, or a GTK/shell theme) is compiled/loaded once
    # at session start, so it activates only after logging out and back in.
    if any(_RELOGIN_RE.search(p) for p in file_list):
        return {
            "requirement": "relogin",
            "services": [],
            "reason": (
                f"{package_name} ships a desktop-shell payload (shell "
                f"extension or theme) the running session loads only at "
                f"login — log out and back in to activate it"
            ),
        }
    if not units:
        return {
            "requirement": "none",
            "services": [],
            "reason": "no service units in package manifest",
        }
    return {
        "requirement": "none",
        "services": [],
        "reason": (
            "service units present in package but none are currently active"
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


# 3.0-F28: width of the loud post-transaction reboot banner rule. 72 keeps it
# inside an 80-col terminal with margin.
_REBOOT_BANNER_WIDTH = 72


def format_reboot_banner(package_names):
    """Render the LOUD, aggregated post-transaction reboot banner (3.0-F28).

    Args:
        package_names: iterable of just-installed/upgraded package names that
            require a reboot to activate. Aggregated across the WHOLE
            transaction — a single banner, printed once at the end, lists every
            reboot-gated package rather than one line lost per-package in the
            install scroll.

    Returns:
        Multi-line banner string, or "" when package_names is empty (nothing
        needs a reboot — print nothing). The caller prints this verbatim after
        the transaction completes.

    Prime Directive: a package whose payload cannot activate until reboot must
    never install silently. The live failure this closes: nvidia's kernel
    modules install behind the nouveau blacklist and stay inactive — the old
    driver keeps running — with no notice that a reboot is what makes the new
    driver take over.
    """
    names = sorted({n for n in package_names if n})
    if not names:
        return ""
    rule = "=" * _REBOOT_BANNER_WIDTH
    lines = [
        rule,
        "  REBOOT REQUIRED",
        rule,
        "  The following just-installed/upgraded package(s) ship a payload",
        "  that CANNOT activate on the running system until you reboot:",
        "",
    ]
    lines.extend(f"    - {name}" for name in names)
    lines.extend([
        "",
        "  Until you reboot, these components are on disk but NOT yet active",
        "  (for example, a newly installed GPU driver stays behind the driver",
        "  the running kernel already loaded).",
        "",
        "  Run: sudo reboot",
        rule,
    ])
    return "\n".join(lines)


def reboot_required_names(db, candidate_names):
    """Return the subset of candidate_names that require a reboot to activate.

    A package qualifies if its installed row declares reboot_required (3.0-F28,
    the authoritative per-package signal) OR its name is in the structural
    REBOOT_TRIGGER_PACKAGES set (OS core: kernel/glibc/systemd/microcode/
    bootloader). Unions both so neither a declared out-of-tree driver nor a
    structural boot-path package is missed.

    Args:
        db: PackageDB — queried for each candidate's installed row.
        candidate_names: iterable of package names installed/upgraded in the
            just-completed transaction.

    Returns:
        list[str] of names needing a reboot (order not guaranteed; the banner
        sorts). Empty when none qualify.
    """
    out = []
    for name in dict.fromkeys(candidate_names):  # de-dupe, preserve order
        if name in REBOOT_TRIGGER_PACKAGES:
            out.append(name)
            continue
        row = db.get_installed(name)
        if row and row.get("reboot_required"):
            out.append(name)
    return out


def _loud(text):
    """Paint one line in the bold amber this project reserves for "act on me".

    Same escape sequences pkm/output.py uses for its error and warning
    prefixes, so a terminal that shows one shows the other. Applied ONLY to
    the reboot section: colour is a severity signal and spending it on the
    parts of the block that are not urgent is how it stops meaning anything.

    The reason this exists: the REBOOT REQUIRED block printed in the same
    monochrome as the thousands of lines of install output around it, and was
    read straight past. The project's model for a message a user must not
    miss is the coloured disk-unlock prompt.
    """
    from .output import _C_BOLD, _C_RESET, _C_YELLOW
    return f"{_C_BOLD}{_C_YELLOW}{text}{_C_RESET}"


def format_next_steps(classifications, estimate=False, color=False):
    """Render ONE consolidated "Next steps" block.

    Aggregates every package touched into a single, strongest-first advisory
    rather than a per-package line lost in the scroll. Sections, in precedence
    order: REBOOT (the machine must reboot), RESTART SERVICES (running daemons
    carry old code until restarted), LOG OUT AND BACK IN (a desktop-shell
    payload activates only at login), then a tally of packages that are already
    fully active and need nothing.

    Args:
        classifications: list of (package_name, classification_dict) pairs, one
            per package — each dict as returned by classify_restart_requirement.
        estimate: when True, render as a PRE-transaction ESTIMATE — a distinct
            banner + a note that the authoritative list prints after the
            transaction. cmd_upgrade's plan summary passes this (classified from
            the currently-installed manifests, since the new files are not on
            disk yet); the end-of-transaction block leaves it False and is
            authoritative. Same classifier + renderer either way, so the
            estimate and the final advisory read consistently — only the framing
            says which one you are looking at.

    Returns:
        Multi-line block string, or "" when NOTHING is actionable (no reboot /
        restart / relogin required). The "Active now" tally is shown only
        alongside a real action section — it never renders alone.

    Prime Directive: an upgrade that ships new code the running system is not
    yet using must say so plainly — the user decides when to take the reboot,
    restart, or re-login, but is never left to discover the divergence later.
    """
    reboot_names = []
    restart_services = []
    relogin_names = []
    none_count = 0
    for name, c in classifications:
        req = c.get("requirement")
        if req == "reboot":
            reboot_names.append(name)
        elif req == "restart":
            restart_services.extend(c.get("services", []))
        elif req == "relogin":
            relogin_names.append(name)
        else:
            none_count += 1

    if not (reboot_names or restart_services or relogin_names):
        return ""

    rule = "=" * _REBOOT_BANNER_WIDTH
    if estimate:
        lines = [
            rule,
            "  NEXT STEPS (ESTIMATE — before upgrade)",
            rule,
            "",
            "  Estimated from the currently-installed files; the authoritative",
            "  list prints after the upgrade completes.",
        ]
    else:
        lines = [rule, "  NEXT STEPS", rule]

    if reboot_names:
        paint = _loud if color else (lambda text: text)
        lines.append("")
        lines.append(paint(
            "  REBOOT REQUIRED — these package(s) ship a payload that"))
        lines.append(paint(
            "  cannot activate on the running system until you reboot:"))
        lines.extend(f"    - {n}" for n in sorted(set(reboot_names)))
        lines.append(paint("  Run: sudo reboot"))

    if restart_services:
        # De-dupe unit names preserving discovery order across packages.
        seen = set()
        units = [
            s for s in restart_services if not (s in seen or seen.add(s))
        ]
        lines.append("")
        lines.append("  RESTART SERVICES — running service(s) were upgraded and")
        lines.append("  keep the old code in memory until restarted:")
        lines.extend(f"    - {u}" for u in units)
        lines.append("  Run: pkm restart-services --all")

    if relogin_names:
        lines.append("")
        lines.append("  LOG OUT AND BACK IN — desktop-shell payload(s) the running")
        lines.append("  session loads only at login:")
        lines.extend(f"    - {n}" for n in sorted(set(relogin_names)))

    if none_count:
        lines.append("")
        lines.append(f"  Active now (no action): {none_count} package(s)")

    lines.append(rule)
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
            if _TRACE_AVAILABLE:
                result = _trace.traced_run(
                    ["systemctl", "restart", unit],
                    timeout=60, phase="pkm_service_restart",
                    intent=f"systemctl restart {unit}",
                )
            else:
                result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                    ["systemctl", "restart", unit],
                    capture_output=True, text=True, timeout=60,
                )
            results[unit] = result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            results[unit] = False
    return results

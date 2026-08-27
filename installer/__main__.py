# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Forge — InterGenOS System Installer — Entry point + mode dispatcher.

Forge runs in three modes, dispatched by `igos.installer=` on the kernel cmdline
(set by GRUB menu) or by an explicit `--mode` flag:

  * `gui` — GTK4/libadwaita 9-screen installer (Welcome → Keyboard/Locale/TZ →
            Disk → User → Packages → Graphics → Confirm → Progress → Done).
            Default when launched
            from a Wayland session (`igos.installer=gui` from GRUB).
  * `tui` — Declarative-builder TUI on tty1. Walks the user through a small
            set of questions via dialog(1), emits a yaml config at install
            time, then runs the install non-interactively against that yaml
            (with disk + password prompted inline). `igos.installer=tui` from
            GRUB.
  * `live` — No install; the live-session "Install InterGenOS" launcher icon
             on the GNOME desktop hands off here for explicit user-initiated
             installs from within the live session. Detection: no
             `igos.installer=` cmdline param (or `igos.mode=try` per the
             3-entry GRUB menu).

Usage:
    forge --archives /var/lib/igos/archives [options]
    forge --mode gui --archives ...
    forge --mode tui --archives ...

When invoked without `--mode`, mode is auto-selected:
    1. `igos.installer=` on /proc/cmdline (set by GRUB)  — strongest signal
    2. `WAYLAND_DISPLAY` env  → GUI
    3. tty1                   → TUI
    4. fallback                → GUI (matches modal expectation)
"""

import argparse
import os
import sys
from pathlib import Path


def parse_cmdline_installer_mode():
    """Parse `igos.installer={gui,tui}` from /proc/cmdline. Returns mode string
    or None.

    Also recognises `igos.mode=try` as a signal that the user is in live mode
    (no install dispatched) — returns the literal string "live" in that case.
    """
    try:
        with open("/proc/cmdline", "r", encoding="utf-8") as f:
            cmdline = f.read()
    except (FileNotFoundError, PermissionError):
        return None

    for tok in cmdline.split():
        if tok.startswith("igos.installer="):
            val = tok.split("=", 1)[1].strip()
            if val in ("gui", "tui"):
                return val
        elif tok == "igos.mode=try":
            # In "Try InterGenOS" mode, no installer auto-launch unless an
            # `igos.installer=` is also present.
            pass

    return None


def detect_session_mode():
    """Heuristic mode for when no cmdline directive sets it.

    Wayland session active → GUI.
    Otherwise (running on a tty without a graphical session) → TUI.
    """
    if os.environ.get("WAYLAND_DISPLAY"):
        return "gui"

    # XDG_SESSION_TYPE is sometimes set even on tty1 if the user logged in
    # via systemd-logind. Trust WAYLAND_DISPLAY over it.
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "gui"

    return "tui"


def resolve_mode(arg_mode):
    """Resolve effective mode.

    Priority: explicit --mode > /proc/cmdline igos.installer= > session heuristic.
    """
    if arg_mode:
        return arg_mode

    cmdline_mode = parse_cmdline_installer_mode()
    if cmdline_mode:
        return cmdline_mode

    return detect_session_mode()


def dispatch(mode, archive_dir, packages_dir, dry_run):
    """Hand off to the right frontend.

    Imports are lazy per-mode so that (e.g.) gi/Gtk imports don't get
    pulled in for a TUI install. Keeps the import graph honest about which
    backends each frontend actually needs.
    """
    if mode == "live":
        print("forge: live mode — no install dispatched.")
        print("       Click the 'Install InterGenOS' launcher on the desktop "
              "to start an install from the live session.")
        return 0

    if mode == "service":
        # Architecture B (2026-05-25): the root-privileged D-Bus install
        # backend. Activated on-demand by dbus-daemon when the GUI calls
        # org.intergenos.ForgeInstaller1. Delegates to backend_service.main()
        # which configures logging.basicConfig + LOG.info startup banner +
        # uid check. Earlier code instantiated ForgeInstallerService directly
        # which (a) silently bypassed the logging config (so journal was
        # empty for every backend session) and (b) overrode the service's
        # own default packages_dir with a hardcoded /var/lib/igos/packages
        # — pkm-DB flat layout that produced "archive resolution error" on
        # every install attempt (the service default is the correct tier-
        # aware /usr/share/intergenos/installer-hooks path).
        #
        # Pass-through of --archives / --packages still works because main()
        # constructs the service from its module-level DEFAULTs unless
        # overridden via constructor args. Future shape: thread archive_dir
        # / packages_dir into main() if explicit overrides are needed
        # (forge-installer-backend.service currently passes neither).
        from .backend_service import main as backend_main
        return backend_main()

    if mode == "gui":
        # Direct import from .window keeps gi/Gtk imports lazy — the gui
        # package's __init__.py stays empty so test rigs can import
        # `installer.frontend.gui.state` without pulling in PyGObject.
        from .frontend.gui.window import run_installer as run_gui
        run_gui(archive_dir, packages_dir, dry_run=dry_run)
        return 0

    if mode == "tui":
        from .frontend.tui import run_installer as run_tui
        run_tui(archive_dir, packages_dir, dry_run=dry_run)
        return 0

    print(f"forge: unknown mode: {mode}", file=sys.stderr)
    return 2


def build_parser():
    """Forge's real argument parser, built without running the installer.

    Split out of ``main()`` so the parser can be INTROSPECTED — the M4
    capability surface is generated by walking the real parsers of the shipped
    first-party tools (the same shape ``pkm.cli.build_parser`` already had), so
    the gate that checks a reply's `forge ...` command cannot drift from the
    flags forge actually accepts. ``main()`` calls this and behaves exactly as
    before."""
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge — InterGenOS System Installer (GUI or declarative-builder TUI)",
    )
    parser.add_argument(
        "--mode",
        choices=("gui", "tui", "live", "service"),
        default=None,
        help="Force a specific frontend (overrides /proc/cmdline + session heuristic). "
             "`service` runs the root-privileged D-Bus install backend.",
    )
    parser.add_argument(
        "--archives",
        required=False,
        default=None,
        help="Path to .igos.tar.gz package archives "
             "(optional for --mode service; defaults to /var/lib/igos/archives)",
    )
    parser.add_argument(
        "--packages",
        help="Path to packages/ directory (for post-install hooks + tier mapping)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log all destructive commands without executing them",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="Forge 0.2.0-iso-dev (InterGenOS Installer)",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    mode = resolve_mode(args.mode)

    # Mode-specific root + archives gating. Architecture B (2026-05-25):
    #   * gui     — runs as the calling USER (must NOT be root). No
    #               archives needed; the privileged backend has its own
    #               defaults.
    #   * service — runs as ROOT (D-Bus activation guarantees this).
    #               Archives default to /var/lib/igos/archives if absent.
    #   * tui     — must run as root (legacy direct-call architecture).
    #   * live    — no install dispatched; mode/uid checks bypassed.
    if mode == "live":
        return dispatch(mode, None, None, args.dry_run)

    if mode == "gui":
        if os.geteuid() == 0:
            print("ERROR: GUI mode must NOT run as root.", file=sys.stderr)
            print("  Architecture B: GUI is unprivileged, install happens via "
                  "the org.intergenos.ForgeInstaller1 D-Bus backend.",
                  file=sys.stderr)
            sys.exit(1)
        archive_dir = Path(args.archives) if args.archives else None
        packages_dir = Path(args.packages) if args.packages else None
        sys.exit(dispatch(
            mode,
            str(archive_dir) if archive_dir else None,
            str(packages_dir) if packages_dir else None,
            args.dry_run,
        ))

    if mode == "service":
        if os.geteuid() != 0:
            print("ERROR: Service mode must run as root.", file=sys.stderr)
            sys.exit(1)
        archive_dir = Path(args.archives) if args.archives else Path("/var/lib/igos/archives")
        packages_dir = Path(args.packages) if args.packages else Path("/var/lib/igos/packages")
        sys.exit(dispatch(
            mode, str(archive_dir), str(packages_dir), args.dry_run,
        ))

    if mode == "tui":
        if os.geteuid() != 0:
            print("ERROR: TUI mode must run as root (use sudo).", file=sys.stderr)
            sys.exit(1)
        if not args.archives:
            print("ERROR: --archives is required for tui mode.", file=sys.stderr)
            sys.exit(1)
        archive_dir = Path(args.archives)
        if not archive_dir.exists():
            print(f"ERROR: Archive directory not found: {archive_dir}", file=sys.stderr)
            sys.exit(1)
        packages_dir = Path(args.packages) if args.packages else None
        sys.exit(dispatch(
            mode, str(archive_dir),
            str(packages_dir) if packages_dir else None, args.dry_run,
        ))

    print(f"ERROR: unknown mode: {mode}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()

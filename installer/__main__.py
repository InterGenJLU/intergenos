"""Forge — InterGenOS System Installer — Entry point + mode dispatcher.

Forge runs in three modes, dispatched by `igos.installer=` on the kernel cmdline
(set by GRUB menu) or by an explicit `--mode` flag:

  * `gui` — GTK4/libadwaita 7-screen installer (Welcome → Keyboard/Locale/TZ →
            Disk → User → Confirm → Progress → Done). Default when launched
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

    Imports are deferred per-mode so that (e.g.) gi/Gtk imports don't get
    pulled in for a TUI install. Keeps the import graph honest about which
    backends each frontend actually needs.
    """
    if mode == "live":
        print("forge: live mode — no install dispatched.")
        print("       Click the 'Install InterGenOS' launcher on the desktop "
              "to start an install from the live session.")
        return 0

    if mode == "gui":
        from .frontend.gui import run_installer as run_gui
        run_gui(archive_dir, packages_dir, dry_run=dry_run)
        return 0

    if mode == "tui":
        from .frontend.tui import run_installer as run_tui
        run_tui(archive_dir, packages_dir, dry_run=dry_run)
        return 0

    print(f"forge: unknown mode: {mode}", file=sys.stderr)
    return 2


def main():
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge — InterGenOS System Installer (GUI or declarative-builder TUI)",
    )
    parser.add_argument(
        "--mode",
        choices=("gui", "tui", "live"),
        default=None,
        help="Force a specific frontend (overrides /proc/cmdline + session heuristic)",
    )
    parser.add_argument(
        "--archives",
        required=True,
        help="Path to .igos.tar.gz package archives",
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

    args = parser.parse_args()

    archive_dir = Path(args.archives)
    if not archive_dir.exists():
        print(f"ERROR: Archive directory not found: {archive_dir}", file=sys.stderr)
        sys.exit(1)

    packages_dir = Path(args.packages) if args.packages else None

    if os.geteuid() != 0:
        print("ERROR: Forge must be run as root.", file=sys.stderr)
        print("  sudo forge --archives /path/to/archives [--mode gui|tui]",
              file=sys.stderr)
        sys.exit(1)

    mode = resolve_mode(args.mode)
    sys.exit(dispatch(
        mode,
        str(archive_dir),
        str(packages_dir) if packages_dir else None,
        args.dry_run,
    ))


if __name__ == "__main__":
    main()

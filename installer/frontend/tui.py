"""Forge — InterGenOS System Installer — Declarative-builder TUI.

Per Q-TUI-INTERACTIVITY=B + Q-TUI-CONFIG=menu-driven-yaml-at-install (resolved
2026-05-06): the TUI is NOT a "text version of the GUI." It is a builder.

Flow:

    1. walking()           — dialog/whiptail Q&A for the small set of choices
                              that genuinely vary per install: locale,
                              timezone, hostname, optional-package toggles.
    2. emit_yaml()         — answers written to /var/lib/forge/install.yaml
                              (ephemeral; lives on the live overlay).
    3. prompt_install_io() — interactive disk choice + root password + user
                              account during the install proper. Pre-seeding
                              disk = fat-finger risk; pre-seeding password =
                              supply-chain risk. PRIME DIRECTIVE.
    4. run_declarative()   — orchestrate the install non-interactively from
                              the yaml + collected interactive answers, using
                              the existing installer.backend modules.

The walking sequence uses dialog(1) where present, falls back to whiptail(1)
(both in base tier — no new deps). We invoke via subprocess.run; the two
binaries share enough of a flag surface for the wrapper to treat them
interchangeably.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.backend import bootloader, config, disks, hooks, mok, packages, users


YAML_PATH = "/var/lib/forge/install.yaml"
DIALOG_BACKTITLE = "InterGenOS Installer (Forge — Declarative Builder)"


# --------------------------------------------------------------------------
# dialog(1) / whiptail(1) wrappers
# --------------------------------------------------------------------------


def _resolve_dialog_binary():
    """Pick whichever of dialog/whiptail is available. Both honor the same
    --backtitle / --inputbox / --menu / --checklist / --passwordbox / --yesno
    flag set used here. dialog has --stdout (results on stdout); whiptail
    doesn't (results on stderr). We adapt per-binary in _dialog()."""
    for candidate in ("dialog", "whiptail"):
        if shutil.which(candidate):
            return candidate
    return None


_DIALOG_BIN = _resolve_dialog_binary()


def _dialog(*dialog_args):
    """Run dialog/whiptail. Returns (rc, captured-output-or-None).

    Cancelling returns rc != 0; we propagate so callers decide whether to
    abort or re-prompt.
    """
    if _DIALOG_BIN is None:
        raise RuntimeError(
            "Neither 'dialog' nor 'whiptail' is installed on this system. "
            "InterGenOS base tier should ship one — please report this as a "
            "missing-prereq bug."
        )

    if _DIALOG_BIN == "dialog":
        cmd = [_DIALOG_BIN, "--stdout", "--backtitle", DIALOG_BACKTITLE,
               *dialog_args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = proc.stdout
    else:
        # whiptail: results land on stderr. No --stdout flag.
        cmd = [_DIALOG_BIN, "--backtitle", DIALOG_BACKTITLE, *dialog_args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = proc.stderr

    if proc.returncode == 0:
        return 0, out.strip()
    return proc.returncode, None


def _ask_input(title, prompt, default=""):
    return _dialog("--title", title, "--inputbox", prompt, "10", "70", default)


def _ask_password(title, prompt):
    return _dialog("--title", title, "--insecure", "--passwordbox",
                   prompt, "10", "70")


def _ask_menu(title, prompt, items):
    """items: list of (tag, description) tuples. Returns selected tag."""
    args = ["--title", title, "--menu", prompt, "20", "70", str(len(items))]
    for tag, desc in items:
        args.extend([tag, desc])
    return _dialog(*args)


def _ask_checklist(title, prompt, items):
    """items: list of (tag, description, on_or_off) tuples. Returns space-sep
    string of selected tags."""
    args = ["--title", title, "--checklist", prompt, "20", "70", str(len(items))]
    for tag, desc, state in items:
        args.extend([tag, desc, state])
    return _dialog(*args)


def _ask_yesno(title, prompt):
    rc, _ = _dialog("--title", title, "--yesno", prompt, "10", "70")
    return rc == 0


def _show_confirm_summary(cfg, install_io):
    """Show a summary dialog before the destructive install begins.
    Returns True if the user confirms, False if they cancel."""
    disk = install_io.get("disk", "unknown")
    lines = [
        f"Target disk:     {disk} (ALL DATA WILL BE ERASED)",
        f"Hostname:        {cfg.get('hostname', '?')}",
        f"Locale:          {cfg.get('locale', '?')}",
        f"Timezone:        {cfg.get('timezone', '?')}",
        f"Package groups:  {', '.join(cfg.get('package_groups', []))}",
        f"Root password:   (set)",
        f"User account:    {install_io.get('username', '?')}",
    ]
    msg = "\n".join(lines)

    return _ask_yesno(
        "Confirm installation",
        f"Review your choices:\n\n{msg}\n\nProceed with installation?",
    )


def _cleanup_on_abort(yaml_path=None):
    """Clean up any partial state on abort or cancellation."""
    if yaml_path:
        p = Path(yaml_path)
        if p.exists():
            p.unlink(missing_ok=True)
    print("forge: installation cancelled. No changes were made to the target disk.",
          file=sys.stderr)
    return 1


# --------------------------------------------------------------------------
# walking() — small set of yaml-bound questions
# --------------------------------------------------------------------------


LOCALES = [
    ("en_US.UTF-8", "English (United States)"),
    ("en_GB.UTF-8", "English (United Kingdom)"),
    ("de_DE.UTF-8", "German"),
    ("fr_FR.UTF-8", "French"),
    ("es_ES.UTF-8", "Spanish"),
    ("ja_JP.UTF-8", "Japanese"),
    ("zh_CN.UTF-8", "Chinese (Simplified)"),
    ("other",       "Type a custom locale (e.g. nl_NL.UTF-8)"),
]


# Coarse list — enough that most users don't have to "type a custom" path.
TIMEZONES_COMMON = [
    ("UTC",                 "Coordinated Universal Time"),
    ("America/New_York",    "US Eastern"),
    ("America/Chicago",     "US Central"),
    ("America/Denver",      "US Mountain"),
    ("America/Los_Angeles", "US Pacific"),
    ("Europe/London",       "UK"),
    ("Europe/Berlin",       "Central Europe"),
    ("Europe/Paris",        "Western Europe"),
    ("Asia/Tokyo",          "Japan"),
    ("Asia/Shanghai",       "China"),
    ("Australia/Sydney",    "Australia East"),
    ("other",               "Type a custom IANA timezone (e.g. Pacific/Auckland)"),
]


PACKAGE_GROUP_CHOICES = [
    ("core",          "Essential system (kernel, shell, coreutils, systemd)", "on"),
    ("base",          "CLI utilities (htop, rsync, strace, screen)",          "on"),
    ("desktop-gnome", "GNOME desktop environment on Wayland",                  "on"),
    ("extra",         "Browsers, editors, dev tooling",                        "off"),
    ("ai",            "Local AI runtime (llama.cpp + models)",                 "off"),
]


def _ask_locale():
    rc, tag = _ask_menu("Locale", "Choose your system locale:", LOCALES)
    if rc != 0:
        return None
    if tag == "other":
        rc, custom = _ask_input("Custom locale",
                                "Enter a glibc locale (e.g. nl_NL.UTF-8):",
                                "en_US.UTF-8")
        if rc != 0 or not custom:
            return None
        return custom
    return tag


def _ask_timezone():
    rc, tag = _ask_menu("Timezone", "Choose your timezone:", TIMEZONES_COMMON)
    if rc != 0:
        return None
    if tag == "other":
        rc, custom = _ask_input("Custom timezone",
                                "Enter an IANA timezone (e.g. Pacific/Auckland):",
                                "UTC")
        if rc != 0 or not custom:
            return None
        return custom
    return tag


def _ask_hostname():
    rc, hn = _ask_input("Hostname",
                        "Enter the hostname for this system:",
                        "intergenos")
    if rc != 0 or not hn:
        return None
    return hn


def _ask_package_groups():
    rc, sel = _ask_checklist(
        "Package groups",
        "Select package groups to install (space toggles, enter accepts):",
        PACKAGE_GROUP_CHOICES,
    )
    if rc != 0 or sel is None:
        return None
    # `core` is required; force-include it even if user un-toggled.
    chosen = set(sel.split()) | {"core"}
    return sorted(chosen)


def walking():
    """Run the interactive walking sequence. Returns dict of answers (or None
    if the user cancelled at any step)."""
    locale = _ask_locale()
    if locale is None:
        return None

    timezone = _ask_timezone()
    if timezone is None:
        return None

    hostname = _ask_hostname()
    if hostname is None:
        return None

    groups = _ask_package_groups()
    if groups is None:
        return None

    return {
        "version": 1,
        "locale": locale,
        "timezone": timezone,
        "hostname": hostname,
        "package_groups": groups,
    }


# --------------------------------------------------------------------------
# emit_yaml — answers → /var/lib/forge/install.yaml
# --------------------------------------------------------------------------


def emit_yaml(answers, path=YAML_PATH):
    """Write answers as yaml without taking on a yaml dependency.

    Schema is small and stable — the hand-rolled writer keeps the install-time
    surface free of optional package deps. yaml READING (in run_declarative)
    uses PyYAML which is already a dep.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as f:
        f.write("# Forge install config — generated at install time by the\n")
        f.write("# declarative-builder TUI. Ephemeral (lives on the live\n")
        f.write("# overlay; not persisted to the installed target).\n")
        f.write(f"version: {answers['version']}\n")
        f.write(f"locale: \"{answers['locale']}\"\n")
        f.write(f"timezone: \"{answers['timezone']}\"\n")
        f.write(f"hostname: \"{answers['hostname']}\"\n")
        f.write("package_groups:\n")
        for group in answers["package_groups"]:
            f.write(f"  - {group}\n")

    return out


def _load_yaml(path):
    """Read the yaml back. Uses PyYAML (already an installer dep)."""
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------
# Interactive disk + password collection (NOT yaml — Q-TUI-INTERACTIVITY=B)
# --------------------------------------------------------------------------


def prompt_install_io():
    """Collect disk choice + root password + user account interactively.

    Returns (disk, root_password, username, user_password, mok_password) or
    None if the user cancelled.
    """
    # Disk selection — list candidates from disks.list_candidates() (existing
    # backend helper). Filter out the live media device.
    try:
        candidates = disks.list_candidates()
    except AttributeError:
        # If list_candidates doesn't exist yet, fall back to a plain text input.
        # TODO Q1 (per dispatch): SPOC will weigh in on disk-detection logic
        # in a follow-up phase; sketching as TODO here.
        rc, raw = _ask_input(
            "Target disk",
            "Enter the disk device to install to (e.g. /dev/nvme0n1, /dev/sda):",
            "/dev/sda",
        )
        if rc != 0 or not raw:
            return None
        disk = raw
    else:
        items = [(d.path, f"{d.size_gb} GB — {d.model}") for d in candidates]
        if not items:
            print("ERROR: no disks detected. Aborting.", file=sys.stderr)
            return None
        rc, disk = _ask_menu("Target disk",
                             "Select the disk to install to (DESTRUCTIVE):",
                             items)
        if rc != 0 or not disk:
            return None

    # Confirm destructive op
    if not _ask_yesno(
        "Confirm destructive operation",
        f"Installing to {disk} WILL ERASE all existing data on it.\n\n"
        f"Continue?",
    ):
        return None

    # Root password
    rc, root_pw = _ask_password("Root password", "Enter the root password:")
    if rc != 0 or not root_pw:
        return None

    # User account
    rc, username = _ask_input("User account",
                              "Enter the primary user's username:",
                              "user")
    if rc != 0 or not username:
        return None

    rc, user_pw = _ask_password("User password",
                                f"Enter the password for {username}:")
    if rc != 0 or not user_pw:
        return None

    # MOK enrollment password (Secure Boot — only collected if EFI; rest of
    # backend skips this on BIOS legacy)
    mok_pw = ""
    if disks.is_efi():
        rc, mok_pw = _ask_password(
            "Secure Boot MOK password",
            "Enter a one-time password to enroll the InterGenOS Machine Owner "
            "Key in your firmware. You'll be prompted for this password "
            "during the first reboot via MokManager:",
        )
        if rc != 0 or not mok_pw:
            return None

    return {
        "disk": disk,
        "root_password": root_pw,
        "username": username,
        "user_password": user_pw,
        "mok_password": mok_pw,
    }


# --------------------------------------------------------------------------
# Declarative install runner
# --------------------------------------------------------------------------


def run_declarative(yaml_path, install_io, archive_dir, packages_dir, dry_run):
    """Read yaml + interactive answers, run the install non-interactively.

    Thin wrapper over `installer.backend.install.run_install` — the canonical
    Phase 4 orchestrator shared by both TUI and GUI frontends. This function
    only handles build-style stdout rendering; all backend orchestration
    (12-phase pipeline, failure rollback, MOK keypair sequencing, etc.) is
    in the orchestrator.
    """
    from installer.backend import install as backend_install

    cfg = _load_yaml(yaml_path)

    print(f"forge: declarative install starting from {yaml_path}")
    print(f"       locale={cfg['locale']} tz={cfg['timezone']} "
          f"hostname={cfg['hostname']} groups={cfg['package_groups']}")
    print(f"       target disk={install_io['disk']}")
    if dry_run:
        print("forge: --dry-run set — destructive disk ops will log only.")

    phases_total = len(backend_install.PHASE_ORDER)

    def _progress(phase, current, total, message):
        if phase == backend_install.PHASE_PACKAGES and total != phases_total:
            # per-package fanout from packages.install_packages
            print(f"        ({current}/{total}) {message}")
            return
        if phase == backend_install.PHASE_HOOKS and total != phases_total:
            # per-hook fanout from hooks.run_post_install_hooks
            print(f"        ({current}/{total}) {message}")
            return
        # Phase-boundary event. current==phase-index on enter, ==index+1 on exit.
        # Render two-line shape: enter ("[ ... ]"), exit ("[ OK  ]").
        if current == 0 or current < phases_total and message and "WARN" not in message and current < phases_total:
            tag = "[ ... ]" if current < total else "[ OK  ]"
        else:
            tag = "[ OK  ]"
        if "WARN" in message or "failed" in message.lower():
            tag = "[ WARN ]"
        print(f"  {tag} {phase}: {message}")

    result = backend_install.run_install(
        yaml_path, install_io,
        str(archive_dir) if archive_dir else None,
        str(packages_dir) if packages_dir else None,
        progress_callback=_progress,
        dry_run=dry_run,
    )

    print()
    if result.success:
        print("forge: install complete.")
        if result.package_fail_count:
            print(f"       (note: {result.package_fail_count} package(s) failed)")
            for n, msg in result.failed_packages:
                print(f"         FAILED: {n}: {msg}")
        print("       Reboot, remove the install media, and (if EFI) follow the")
        print("       MokManager prompts to enroll the InterGenOS vendor cert.")
        return 0

    print(f"forge: install FAILED at phase {result.phase_completed or '<pre-validation>'}")
    print(f"       error: {result.error_message}")
    return 1


# --------------------------------------------------------------------------
# Entry point (called from installer/__main__.py via dispatch)
# --------------------------------------------------------------------------


def run_installer(archive_dir, packages_dir=None, dry_run=False):
    """Orchestrate the declarative-builder TUI: walk → emit → prompt → confirm → run."""
    # Walking — yaml-bound choices
    answers = walking()
    if answers is None:
        return _cleanup_on_abort()

    yaml_path = emit_yaml(answers)
    print(f"forge: install config written to {yaml_path}")

    # Interactive — disk + passwords (Q-TUI-INTERACTIVITY=B)
    install_io = prompt_install_io()
    if install_io is None:
        return _cleanup_on_abort(yaml_path=str(yaml_path))

    # Confirm summary — last chance before destructive install
    if not _show_confirm_summary(answers, install_io):
        return _cleanup_on_abort(yaml_path=str(yaml_path))

    rc = run_declarative(str(yaml_path), install_io, archive_dir,
                         packages_dir, dry_run)

    # Reboot prompt
    if rc == 0:
        if _ask_yesno(
            "Installation complete",
            "Installation completed successfully.\n\n"
            "Reboot now to boot into your new InterGenOS system?"
        ):
            print("forge: rebooting...")
            subprocess.run(["reboot"], check=False)
        else:
            print("forge: you can reboot later by running 'reboot' or Ctrl+Alt+Del.")

    # Clean up the ephemeral yaml
    Path(str(yaml_path)).unlink(missing_ok=True)
    return rc


# Legacy compatibility — the original `run_installer` signature is preserved.
# If a caller passes no positional `dry_run`, default behaviour is unchanged.

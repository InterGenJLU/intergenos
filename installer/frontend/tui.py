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
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.backend import bootloader, config, disks, hooks, mok, packages, users


YAML_PATH = "/var/lib/forge/install.yaml"
DIALOG_BACKTITLE = "InterGenOS Installer (Forge — Declarative Builder)"

# Install-time integrity verification paths. The manifest + release-key
# public component live on the install media (placed there by the build's
# `manifest` phase + signing ceremony). The audit log lives in the install
# environment; PHASE_CLEANUP copies it onto the target's /var/log so the
# user has a record on their installed system of what (if anything) they
# overrode during install.
INSTALL_MEDIA_MANIFEST = Path("/install/intergenos-archive-manifest.txt")
INSTALL_MEDIA_PUBKEY = Path("/install/intergenos-release-key.asc")
INTEGRITY_AUDIT_LOG = Path("/var/log/igos-integrity-override.log")


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
    # The `--insecure` flag tells dialog(1) to show one asterisk per typed
    # character (so the user has visual feedback of how many chars they've
    # typed). whiptail(1) doesn't recognize the flag but ignores unknown
    # options gracefully — its default passwordbox already shows asterisks,
    # so behavior is equivalent. Don't drop `--insecure` thinking it's a
    # no-op on whiptail; it's load-bearing for dialog(1) UX.
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
    luks_line = (
        "LUKS encryption: ENABLED (passphrase required at every boot)"
        if install_io.get("luks_enabled")
        else "LUKS encryption: disabled"
    )
    lines = [
        f"Target disk:     {disk} (ALL DATA WILL BE ERASED)",
        luks_line,
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
    from installer.backend._validators import validate_hostname
    while True:
        rc, hn = _ask_input("Hostname",
                            "Enter the hostname for this system:",
                            "intergenos")
        if rc != 0 or not hn:
            return None
        err = validate_hostname(hn)
        if err is None:
            return hn
        _dialog("--title", "Invalid hostname", "--msgbox", err, "10", "70")


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
    # Disk selection — enumerate via disks.detect_disks() (returns list of
    # Disk dataclass instances with .path / .size_human / .model). Live media
    # is excluded by detect_disks (root-disk filter in backend/disks.py:113).
    # If detection returns no candidates (timeout or no eligible disks),
    # fall back to plain text input so the install path stays openable
    # on edge hardware.
    candidates = disks.detect_disks()
    if candidates:
        items = [(d.path, f"{d.size_human} — {d.model}") for d in candidates]
        rc, disk = _ask_menu("Target disk",
                             "Select the disk to install to (DESTRUCTIVE):",
                             items)
        if rc != 0 or not disk:
            return None
    else:
        rc, raw = _ask_input(
            "Target disk",
            "No disks auto-detected. Enter the disk device to install to "
            "(e.g. /dev/nvme0n1, /dev/sda):",
            "/dev/sda",
        )
        if rc != 0 or not raw:
            return None
        disk = raw

    # Confirm destructive op
    if not _ask_yesno(
        "Confirm destructive operation",
        f"Installing to {disk} WILL ERASE all existing data on it.\n\n"
        f"Continue?",
    ):
        return None

    # D-001 LUKS opt-in. Default = unencrypted (matches D-001 ratified
    # "opt-in not default" semantics). When the user opts in, capture
    # passphrase + confirm, surface entropy guidance + a forgotten-passphrase
    # warning. The passphrase is NEVER stored to disk by the TUI — it
    # rides the install_io dict to disks.partition_disk which pipes it
    # to cryptsetup via stdin (not argv) and zeroizes its copy after use.
    luks_enabled = _ask_yesno(
        "Full-disk encryption (LUKS)",
        "Encrypt the root filesystem with LUKS2?\n\n"
        "If yes, you will be asked for the passphrase at every boot.\n"
        "If you forget the passphrase, your data is unrecoverable.\n\n"
        "Recommended for laptops + portable devices.",
    )
    luks_passphrase = ""
    if luks_enabled:
        # Pre-prompt guidance — character-class diversity + length both
        # increase argon2id cost relative to a brute-force attacker. 12+
        # characters with at least 3 character classes is the standard
        # NIST 800-63B baseline for memorized secrets; passphrases of
        # 4+ unrelated words (the diceware pattern) also work well.
        while True:
            rc, pp1 = _ask_password(
                "FDE passphrase",
                "Enter the disk-encryption passphrase.\n\n"
                "Length matters more than complexity: 12+ characters,\n"
                "or 4+ unrelated dictionary words, are good baselines.",
            )
            if rc != 0 or not pp1:
                return None
            rc, pp2 = _ask_password(
                "Confirm FDE passphrase",
                "Re-enter the same passphrase to confirm:",
            )
            if rc != 0 or pp2 is None:
                return None
            if pp1 != pp2:
                # Mismatch — surface + retry. Don't keep either copy.
                _dialog("--title", "Passphrase mismatch", "--msgbox",
                        "The two passphrases did not match. Try again.",
                        "10", "70")
                pp1 = pp2 = ""
                continue
            warning = _luks_passphrase_warning(pp1)
            if warning:
                # Soft-warning path — operator can accept or re-enter.
                # "No" returns them to the entry prompt. "Yes" accepts.
                accept = _ask_yesno(
                    "Weak passphrase",
                    f"{warning}\n\nAccept this passphrase anyway?",
                )
                if not accept:
                    pp1 = pp2 = ""
                    continue
            luks_passphrase = pp1
            # Drop the local references to the confirm copy + intermediate
            # variable promptly so the in-memory residue is minimized.
            del pp1, pp2
            break

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

    install_io = {
        "disk": disk,
        "root_password": root_pw,
        "username": username,
        "user_password": user_pw,
        "mok_password": mok_pw,
    }
    if luks_enabled:
        install_io["luks_enabled"] = True
        install_io["luks_passphrase"] = luks_passphrase
    return install_io


def _luks_passphrase_warning(passphrase):
    """Return a single human-readable warning string for a weak LUKS
    passphrase, or empty string if no warning fires.

    Heuristics (not a hard reject — operator decides):
      - Length < 8: surface as critically weak
      - Length 8-11 with only one character class: surface as marginal
      - Otherwise: no warning

    NIST 800-63B + LUKS argon2id cost (1 GB memory, t=4) together make
    "long-enough" passphrases the dominant defense. We don't try to
    score complexity beyond character-class counting + length — that
    would be cargo-cult security theater (most real passphrase entropy
    estimators are weak heuristics dressed up as math).
    """
    if not passphrase:
        return "Empty passphrases are not accepted."
    if len(passphrase) < 8:
        return (
            f"Passphrase is {len(passphrase)} characters — well under the "
            "8-character floor. Even with argon2id KDF cost, short "
            "passphrases fall to dictionary attack quickly."
        )
    classes = sum(
        bool(any(test(c) for c in passphrase))
        for test in (str.isupper, str.islower, str.isdigit,
                     lambda c: not c.isalnum())
    )
    if len(passphrase) < 12 and classes < 2:
        return (
            f"Passphrase is {len(passphrase)} characters with only one "
            "character class. Consider lengthening it or adding mixed "
            "character types."
        )
    return ""


# --------------------------------------------------------------------------
# Declarative install runner
# --------------------------------------------------------------------------


def _tui_integrity_warning_callback(package_name, expected_sha256, actual_sha256):
    """Render the hard-coded integrity-mismatch warning to stdout.

    Called by integrity.verify_archives() when an archive's sha doesn't
    match the manifest. The template is hard-coded in the integrity
    module — the TUI just fills in the four placeholders and prints.
    """
    from installer.backend.integrity import (
        INTEGRITY_WARNING_TEMPLATE,
        expected_override_phrase,
    )
    print(INTEGRITY_WARNING_TEMPLATE.format(
        package=package_name,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        override_phrase=expected_override_phrase(package_name),
    ), file=sys.stderr)


def _tui_integrity_ack_callback(package_name):
    """Read user's typed-phrase response from stdin.

    Returns True iff the user typed expected_override_phrase(package_name)
    exactly (case-sensitive, whitespace-trimmed). Anything else (including
    Ctrl+C / EOF) returns False, which aborts the install.
    """
    from installer.backend.integrity import expected_override_phrase
    expected = expected_override_phrase(package_name)
    try:
        line = input("Type override phrase to proceed (or anything else to abort): ")
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    return line.strip() == expected


def _build_verify_config_if_present():
    """Return a VerifyConfig if install-media manifest+key exist, else None.

    Production install media has the manifest + release-key public component
    placed by the build's `manifest` phase + signing ceremony. Dev/test
    environments without those files skip integrity verification.

    Note: skipping when the files are missing is the LOCAL choice for the
    TUI default. Production deployments that want to fail-closed on missing
    manifest can pre-flight-check the paths and bail before invoking the TUI.
    """
    from installer.backend.install import VerifyConfig
    if not INSTALL_MEDIA_MANIFEST.exists() or not INSTALL_MEDIA_PUBKEY.exists():
        return None
    return VerifyConfig(
        manifest_path=INSTALL_MEDIA_MANIFEST,
        public_key_path=INSTALL_MEDIA_PUBKEY,
        audit_log_path=INTEGRITY_AUDIT_LOG,
        warning_callback=_tui_integrity_warning_callback,
        ack_callback=_tui_integrity_ack_callback,
    )


def run_declarative(yaml_path, install_io, archive_dir, packages_dir, dry_run):
    """Read yaml + interactive answers, run the install non-interactively.

    Thin wrapper over `installer.backend.install.run_install` — the canonical
    Phase 4 orchestrator shared by both TUI and GUI frontends. This function
    only handles build-style stdout rendering; all backend orchestration
    (13-phase pipeline including PHASE_VERIFY, failure rollback, MOK keypair
    sequencing, etc.) is in the orchestrator.

    Integrity verification: built-in. If the install media has a signed
    manifest at /install/intergenos-archive-manifest.txt + release-key at
    /install/intergenos-release-key.asc, PHASE_VERIFY runs before any disk
    write. Mismatches surface via _tui_integrity_warning_callback +
    _tui_integrity_ack_callback (typed-phrase override per design doc §6.4).
    Dev/test environments without those files skip the phase silently.
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
        # Phase-boundary event. Orchestrator emits two events per phase:
        # enter at current==phase_idx, exit at current==phase_idx+1. Render
        # two-line shape by checking which side of the phase index we're on.
        phase_idx = backend_install.PHASE_ORDER.index(phase)
        if "WARN" in message or "failed" in message.lower():
            tag = "[ WARN ]"
        elif current == phase_idx:
            tag = "[ ... ]"
        elif current == phase_idx + 1:
            tag = "[ OK  ]"
        else:
            tag = "[ ... ]"
        print(f"  {tag} {phase}: {message}")

    verify_config = None if dry_run else _build_verify_config_if_present()
    if verify_config is not None:
        print("forge: integrity verification armed (signed manifest detected on install media).")
    elif not dry_run:
        print("forge: integrity verification skipped (no signed manifest on install media).")

    result = backend_install.run_install(
        yaml_path, install_io,
        str(archive_dir) if archive_dir else None,
        str(packages_dir) if packages_dir else None,
        progress_callback=_progress,
        dry_run=dry_run,
        verify_config=verify_config,
    )

    print()
    if result.success:
        print("forge: install complete.")
        if result.integrity_overrides_granted:
            print(f"       ⚠ {result.integrity_overrides_granted} integrity override(s) granted during install.")
            print(f"         Review {INTEGRITY_AUDIT_LOG} on the installed system for details.")
        if result.package_fail_count:
            print(f"       (note: {result.package_fail_count} package(s) failed)")
            for n, msg in result.failed_packages:
                print(f"         FAILED: {n}: {msg}")
        print("       Reboot, remove the install media, and (if EFI) follow the")
        print("       MokManager prompts to enroll your machine's MOK with the")
        print("       firmware. See docs/users/secure-boot-and-mok.md.")
        return 0

    if result.integrity_aborted_at:
        print(f"forge: install ABORTED during integrity verification at {result.integrity_aborted_at}")
        print(f"       error: {result.error_message}")
        print(f"       no changes were made to the target disk.")
        return 1

    print(f"forge: install FAILED at phase {result.phase_completed or '<pre-validation>'}")
    print(f"       error: {result.error_message}")
    return 1


# --------------------------------------------------------------------------
# Entry point (called from installer/__main__.py via dispatch)
# --------------------------------------------------------------------------


DEBUG_LOG_PATH = "/var/log/igos-install-debug.log"


def _write_debug_log(exc_type, exc):
    """Append a timestamped traceback to the install-debug log.

    Best-effort: if the log can't be written (read-only fs, permission,
    disk full), swallow silently so we don't mask the original exception
    in run_installer's outer handler. Returns True iff the write succeeded
    so the caller can decide whether to advertise the path to the user.
    """
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(
                f"\n=== forge crash {datetime.utcnow().isoformat(timespec='seconds')}Z ===\n"
                f"{exc_type.__name__}: {exc}\n"
            )
            f.write(traceback.format_exc())
        return True
    except Exception:
        return False


def run_installer(archive_dir, packages_dir=None, dry_run=False):
    """Orchestrate the declarative-builder TUI: walk → emit → prompt → confirm → run.

    Top-level try/except wraps the entire flow so the user never sees a raw
    Python traceback mid-install. Two failure classes:
      * KeyboardInterrupt (Ctrl-C) → exit 130 with "Cancelled by user"
      * any other Exception → log traceback to DEBUG_LOG_PATH (best-effort),
        print sanitized "internal error" line, exit 1.

    The yaml at /var/lib/forge/install.yaml is best-effort cleaned up in the
    finally block — even on crash we don't leave stale install state on the
    live overlay.
    """
    yaml_path = None
    try:
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
                if shutil.which("reboot"):
                    print("forge: rebooting...")
                    subprocess.run(["reboot"], check=False)
                else:
                    # Non-systemd init or busybox-only environment: 'reboot'
                    # binary missing. Don't pretend we rebooted.
                    print("forge: 'reboot' command not found on this system.",
                          file=sys.stderr)
                    print("       Please reboot manually (Ctrl+Alt+Del or your "
                          "platform's reboot command).", file=sys.stderr)
            else:
                print("forge: you can reboot later by running 'reboot' or Ctrl+Alt+Del.")

        return rc

    except KeyboardInterrupt:
        print()
        print("forge: cancelled by user. No changes were made to the target disk.",
              file=sys.stderr)
        return 130

    except Exception as e:
        # Last-resort guard. Write the traceback to a debug log (best-effort)
        # so post-incident review has the full stack, but show the user only
        # a sanitized one-liner — they don't need stderr graffiti to know the
        # install failed.
        logged = _write_debug_log(type(e), e)
        print()
        print(f"forge: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        print("forge: install was aborted; the target disk may be in a partial state.",
              file=sys.stderr)
        if logged:
            print(f"       Full traceback at {DEBUG_LOG_PATH}.", file=sys.stderr)
        return 1

    finally:
        # Best-effort yaml cleanup. We don't want a stale /var/lib/forge/install.yaml
        # surviving a crash and getting picked up by an accidental re-launch.
        if yaml_path is not None:
            try:
                Path(str(yaml_path)).unlink(missing_ok=True)
            except Exception:
                pass


# Legacy compatibility — the original `run_installer` signature is preserved.
# If a caller passes no positional `dry_run`, default behaviour is unchanged.

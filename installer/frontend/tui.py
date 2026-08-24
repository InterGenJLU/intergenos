# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
from installer.frontend import netprobe


YAML_PATH = "/var/lib/forge/install.yaml"
def _read_build_id():
    """BUILD_ID from the running medium's os-release, or None (N-6)."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("BUILD_ID="):
                    return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        pass
    return None


DIALOG_BACKTITLE = "InterGenOS Installer (Forge — Declarative Builder)"
_build_id = _read_build_id()
if _build_id:
    DIALOG_BACKTITLE += f" · {_build_id}"


# --------------------------------------------------------------------------
# Console reporter — one consistent line style for Forge's stdout/stderr.
# --------------------------------------------------------------------------
#
# Forge follows the prevailing distro build-output convention (consistent
# section markers + colored status, like emerge / makepkg), keeping the
# detailed default — the VOICE is cleaned up, not the volume.
# One step-line style with aligned labels (modeled on pkm's Reporter.step),
# one severity scheme (error: / warning: / note:, lower-case, apt/dnf-style,
# to stderr), and a single sanctioned verdict marker pair (✓ / ✗) plus the
# Required warning triangle (⚠). Timestamps stay: the operator reads
# the [YYYY-MM-DD HH:MM:SS] prefix as professional. Color is TTY-aware and
# auto-off when stdout/stderr is not a tty or NO_COLOR is set.

# Aligned label column for step lines. Widest label in use is "Integrity"
# (9 chars); +1 leaves a one-space gutter after every label's colon.
_LABEL_WIDTH = 10


def _color_enabled(stream):
    """True iff ANSI color should be emitted on ``stream`` (TTY + no NO_COLOR)."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


class _ForgeReporter:
    """Forge's console emission surface.

    All lines carry a [YYYY-MM-DD HH:MM:SS] timestamp prefix. Informational
    output goes to stdout; warnings/errors go to stderr (so they survive a
    redirect). Color is applied only when the target stream is a TTY and
    NO_COLOR is unset.
    """

    # ANSI codes, applied only on a color-enabled stream.
    _C_DIM = "\033[2m"
    _C_BOLD = "\033[1m"
    _C_GREEN = "\033[32m"
    _C_YELLOW = "\033[33m"
    _C_RED = "\033[31m"
    _C_RESET = "\033[0m"

    def __init__(self, out=None, err=None):
        self._out = out
        self._err = err

    @property
    def out(self):
        return self._out if self._out is not None else sys.stdout

    @property
    def err(self):
        return self._err if self._err is not None else sys.stderr

    @staticmethod
    def _ts():
        return datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

    def _emit(self, stream, body, color=None):
        ts = self._ts()
        if color and _color_enabled(stream):
            print(f"{self._C_DIM}{ts}{self._C_RESET} {color}{body}{self._C_RESET}",
                  file=stream)
        elif _color_enabled(stream):
            print(f"{self._C_DIM}{ts}{self._C_RESET} {body}", file=stream)
        else:
            print(f"{ts} {body}", file=stream)

    def step(self, label, detail=""):
        """A phase/step line with an aligned label, e.g. step('Config', …)."""
        lab = (label + ":").ljust(_LABEL_WIDTH)
        self._emit(self.out, f"{lab} {detail}".rstrip())

    def info(self, text):
        """A plain informational line (no label)."""
        self._emit(self.out, text)

    def detail(self, text):
        """A sub-step / continuation line, indented under its step."""
        self._emit(self.out, f"    {text}")

    def ok(self, text):
        """A success verdict line (✓)."""
        self._emit(self.out, f"✓ {text}", color=self._C_GREEN)

    def note(self, text):
        """A note: line — apt/dnf-style, to stdout."""
        self._emit(self.out, f"note: {text}")

    def warning(self, text):
        """A warning: line — to stderr, led with the ⚠ triangle (bold yellow on a TTY)."""
        self._emit(self.err, f"⚠ warning: {text}", color=self._C_BOLD + self._C_YELLOW)

    def error(self, text):
        """An error: line — to stderr, led with the ✗ mark (bold red on a TTY)."""
        self._emit(self.err, f"✗ error: {text}", color=self._C_BOLD + self._C_RED)


_reporter = _ForgeReporter()

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


def _ask_yesno(title, prompt, default_no=False):
    """Yes/no dialog. `default_no=True` highlights NO (dialog --defaultno)."""
    args = ["--title", title]
    if default_no:
        args.append("--defaultno")
    args.extend(["--yesno", prompt, "10", "70"])
    rc, _ = _dialog(*args)
    return rc == 0


def _show_confirm_summary(cfg, install_io):
    """Show a summary dialog before the destructive install begins.
    Returns True if the user confirms, False if they cancel."""
    disk = install_io.get("disk", "unknown")
    if install_io.get("luks_enabled"):
        unlock_extras = []
        if install_io.get("tpm2_enabled"):
            unlock_extras.append("TPM2-EXPERIMENTAL")
        if install_io.get("fido2_enabled"):
            unlock_extras.append("FIDO2-EXPERIMENTAL")
        extras_tail = f" + {' + '.join(unlock_extras)}" if unlock_extras else ""
        luks_line = f"LUKS encryption: ENABLED{extras_tail}"
    else:
        luks_line = "LUKS encryption: disabled"
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
    _reporter.info("Installation cancelled. No changes were made to the target disk.")
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
    ("extra",         "Apps & virtualization (Firefox, LibreOffice, QEMU)",    "off"),
    ("ai",            "Local AI runtime (InterGen + llama.cpp serving)",       "off"),
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

    # D-010 InterGen AI opt-in (default NO by requirement). The choice
    # is part of walking() for UX symmetry with the GUI Packages screen,
    # but it threads through install_io rather than the yaml: it's a
    # service-enable choice, not a yaml-schema field. main() copies the
    # value from answers into install_io before run_declarative.
    intergen_ai_enable = _ask_yesno(
        "Enable the InterGen AI assistant?",
        "Enable the InterGen AI assistant? (default: NO)\n\n"
        "When enabled, the AI assistant starts at first login. You "
        "can opt in later either by opening the InterGen AI app from "
        "your Applications menu OR by running `intergen setup` in a "
        "terminal. Either path enables the service and downloads the "
        "local AI model (~4–5 GB on standard hardware, larger on "
        "premium tiers). No data leaves your machine.",
        default_no=True,
    )

    # Couple the AI opt-in to the 'ai' package group: enabling the assistant
    # is meaningless without its runtime binaries (intergen, llama-cpp, the
    # model). The GUI Packages screen auto-forces exactly this
    # (screens/packages.py _on_intergen_toggled, "the service needs the
    # binaries"); the TUI's group checklist and this yes/no are otherwise
    # independent, so a user who answers YES here but left 'ai' unchecked would
    # get the service-enable with NONE of its binaries -- the AI silently
    # absent on first boot (the llama-cpp silent-drop class). Force 'ai' in so
    # the opt-in installs what it promises.
    if intergen_ai_enable and "ai" not in groups:
        groups = sorted(set(groups) | {"ai"})

    # D-019 SSH server opt-in (default NO; amends D-007 sshd-default arm).
    # Same install_io threading pattern as intergen_ai_enable. YES enables
    # sshd.service AND opens TCP/22 in /etc/nftables.conf so the server
    # is actually reachable from the network.
    ssh_server_enable = _ask_yesno(
        "Enable the SSH server?",
        "Enable the SSH server? (default: NO)\n\n"
        "When enabled, the SSH server starts at boot and the firewall "
        "is opened on TCP port 22 so you can SSH in from another "
        "machine. Root login over SSH is always blocked (you log in "
        "as your user, sudo to root). You can opt in later by running "
        "`systemctl enable --now sshd` and adding a TCP/22 accept "
        "rule to /etc/nftables.conf.",
        default_no=True,
    )

    # Optional SSH public key (decided 2026-05-22 Option C).
    # Only meaningful when ssh_server_enable=True. If provided,
    # backend writes ~/.ssh/authorized_keys for the new user AND
    # ships a keys-only sshd_config drop-in (password auth disabled).
    # If left blank, password SSH login stays on. Inputbox is one
    # line; user pastes the full key (dialog scrolls horizontally
    # for long keys).
    ssh_public_key = ""
    if ssh_server_enable:
        rc, key_text = _ask_input(
            "SSH public key (optional)",
            "Paste your SSH public key (e.g. `ssh-ed25519 AAAAC3...`).\n\n"
            "If provided, password-based SSH login is disabled and the "
            "server accepts key-based authentication only. Leave blank "
            "to allow password SSH login (easier first-time setup; "
            "weaker against brute-force).",
            default=""
        )
        if rc == 0 and key_text.strip():
            # Minimal validation -- catch obvious paste mistakes
            # (private key, random text) before commit.
            line = key_text.strip().split("\n", 1)[0]
            parts = line.split(None, 2)
            valid_prefixes = (
                "ssh-rsa", "ssh-ed25519", "ssh-dss",
                "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
                "ecdsa-sha2-nistp521",
                "sk-ssh-ed25519@openssh.com",
                "sk-ecdsa-sha2-nistp256@openssh.com",
            )
            if len(parts) >= 2 and parts[0] in valid_prefixes:
                ssh_public_key = line
            else:
                # Invalid; warn but don't block install. Password
                # auth stays on as the fallback.
                _dialog("--title", "SSH public key not recognized",
                        "--msgbox",
                        "That doesn't look like an SSH public key. "
                        "It should start with `ssh-ed25519` / `ssh-rsa` / "
                        "`ecdsa-sha2-nistp256` (etc.). Continuing with "
                        "password-based SSH authentication enabled. You "
                        "can add a key later by appending it to "
                        "~/.ssh/authorized_keys.", "12", "70")

    return {
        "version": 1,
        "locale": locale,
        "timezone": timezone,
        "hostname": hostname,
        "package_groups": groups,
        "intergen_ai_enable": intergen_ai_enable,
        "ssh_server_enable": ssh_server_enable,
        "ssh_public_key": ssh_public_key,
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


def _mok_prompt_text(sb_state):
    """Build the MOK-password prompt body for an EFI install (pure; unit-tested).

    sb_state is installer.backend.secureboot.is_secure_boot_enabled()'s
    tri-state: True (enforcing), False (off), None (unreadable). The three
    branches mirror the GUI Confirm screen's D-2 / D2 wording so both frontends
    state the same consequence:
      - SB ON  : MOK is MANDATORY (hard block elsewhere) — enroll or don't boot.
      - SB OFF : D2/1.22 — optional NOW, but LOUD about the future-Secure-Boot-
                 flip footgun (a later SB enable needs a manual mokutil --import).
      - unknown: optional; note the state could not be read.
    """
    base = (
        "Set a one-time password to enroll the InterGenOS Machine Owner "
        "Key in your firmware. You choose it — it is never generated, shown "
        "back, or logged. Leave it blank to skip enrollment.\n"
        "After the install finishes, re-enable Secure Boot in your UEFI "
        "firmware setup: that is what triggers MokManager on the next boot, "
        "where you type this password to complete enrollment."
    )
    if sb_state is True:
        return base + (
            "\n\n*** Secure Boot is ENABLED on this machine. ***\n"
            "You MUST enroll a MOK or this install will not boot. Leave this "
            "blank only if you intend to cancel and disable Secure Boot in "
            "firmware instead — the installer will not continue without one "
            "of the two. (The supported procedure is to install with Secure "
            "Boot off and turn it back on at the first reboot.)"
        )
    if sb_state is False:
        return base + (
            "\n\n*** Secure Boot is currently OFF, but this is a UEFI system. ***\n"
            "This is the expected state to install in. Set a password now and "
            "enrollment is staged: turn Secure Boot back on at the first "
            "reboot and MokManager will ask for it. Leaving this blank skips "
            "enrollment — the install boots fine now, BUT if you later enable "
            "Secure Boot in firmware, this system will NOT boot until you "
            "enroll a key by hand (mokutil --import)."
        )
    return base + (
        "\n\n(Secure Boot state could not be read — MOK enrollment is optional. "
        "Leave blank to skip; you can enroll later with mokutil.)"
    )


def _resolve_make_default_boot(is_efi, has_other_os, ask_fn):
    """D1 / work-plan 1.25: decide the default-boot-target value for install_io
    (pure; unit-tested).

    Returns None when there is nothing to ask — a non-EFI install, or no foreign
    installed-OS entry was found, or the probe was inconclusive
    (has_other_os is not True). None makes to_install_io/the backend keep
    efibootmgr --create's prepend. Only when a foreign OS entry is present on an
    EFI install do we ask (ask_fn), returning True (become the default) or False
    (keep the machine's prior default). This is the ask-only-when-it-matters gate
    that keeps single-OS installs silent and never silently takes over a
    multi-OS machine's boot default.
    """
    if not is_efi or has_other_os is not True:
        return None
    return bool(ask_fn())


def _resolve_carry_wifi(active_wifi, ask_fn):
    """Wi-Fi carry (2026-07-11): decide the carry_wifi value for install_io
    (pure; unit-tested).

    Returns None when there is nothing to ask — no active Wi-Fi connection in
    the live session, or the probe was inconclusive (active_wifi is None or
    empty). None keeps the key out of install_io, so the backend carries
    nothing. Only when the live session is actually on Wi-Fi do we ask
    (ask_fn), returning True (carry the profile onto the install — the
    recommended default: the user joined that network on this machine on
    purpose) or False (start the installed system with no saved networks).
    Same ask-only-when-it-matters gate as _resolve_make_default_boot.
    """
    if not active_wifi:
        return None
    return bool(ask_fn())


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
    tpm2_enabled = False
    fido2_enabled = False
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

        # D-001 EXPERIMENTAL unlock methods (operator Option A
        # 2026-05-18T22:52Z). TPM2 + FIDO2 compose with the passphrase
        # slot — passphrase always remains a valid fallback. Frontend
        # surfaces both as opt-in with explicit "(EXPERIMENTAL)" markers.
        from installer.backend import disks as _disks
        if _disks.EXPERIMENTAL_UNLOCK_OFFERED and _disks.tpm2_present() and _disks.tpm2_tools_available():
            tpm2_enabled = _ask_yesno(
                "Unlock with TPM2 (EXPERIMENTAL)",
                "Also enroll a TPM2-sealed key bound to the firmware + Secure\n"
                "Boot state (PCR0+PCR7)? On normal boots the system unlocks\n"
                "without a passphrase prompt. Firmware update OR Secure Boot\n"
                "policy change invalidates the seal — falls through to the\n"
                "passphrase prompt automatically.\n\n"
                "EXPERIMENTAL. See docs/users/full-disk-encryption.md.",
            )
        if _disks.EXPERIMENTAL_UNLOCK_OFFERED and _disks.fido2_tools_available():
            fido2_enabled = _ask_yesno(
                "Unlock with FIDO2 token (EXPERIMENTAL)",
                "Also enroll a FIDO2 security token? On boot, plug + touch\n"
                "the token to unlock without typing the passphrase. Token\n"
                "lost / not-plugged / firmware update falls through to the\n"
                "passphrase prompt automatically. You will be prompted to\n"
                "plug + touch the token now during install enrollment.\n\n"
                "EXPERIMENTAL. See docs/users/full-disk-encryption.md.",
            )

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

    # MOK enrollment password (Secure Boot). Collected on EFI; the backend
    # skips this on BIOS legacy (no Secure Boot there).
    #
    # D-2 HARD BLOCK (decided 2026-06-05): when Secure Boot is ENABLED,
    # an install that skips MOK enrollment is guaranteed unbootable, so the
    # installer must not proceed. We don't force anyone to install — but if they
    # do, they must pick a non-bricking path: enroll a MOK here, or cancel and
    # disable Secure Boot in firmware. When Secure Boot is OFF, MOK enrollment
    # is OPTIONAL (a blank password skips it).
    mok_pw = ""
    if disks.is_efi():
        from installer.backend.secureboot import is_secure_boot_enabled
        sb_state = is_secure_boot_enabled()
        sb_on = sb_state is True
        # D2 / work-plan 1.22: the prompt body (incl. the SB-OFF-on-UEFI
        # future-flip consequence) is built by the pure _mok_prompt_text helper.
        rc, mok_pw = _ask_password(
            "Secure Boot MOK password", _mok_prompt_text(sb_state))
        if rc != 0:
            return None  # user cancelled
        if sb_state is False and not mok_pw:
            # D2 / work-plan 1.22 (PI-Z18): UEFI + Secure Boot OFF + blank MOK.
            # Not a block (boots fine now) — confirm the future-flip consequence
            # loudly one more time so the skip is an explicit, informed choice.
            _dialog(
                "--title", "MOK enrollment skipped",
                "--msgbox",
                "You left the MOK password blank on a UEFI system.\n\n"
                "The install will boot fine as-is. But if you LATER enable "
                "Secure Boot in firmware, this system will not boot until you "
                "enroll a key by hand (mokutil --import).\n\n"
                "This is a valid choice — continuing.",
                "13", "72",
            )
        if sb_on and not mok_pw:
            # HARD BLOCK: SB on + no MOK = guaranteed-unbootable install. Refuse
            # to proceed; the user re-runs after enrolling a MOK or disabling SB.
            _dialog(
                "--title", "Cannot continue — Secure Boot is ON",
                "--msgbox",
                "Secure Boot is enabled and no MOK was enrolled. Installing now "
                "would produce a system that cannot boot, so the installer will "
                "not continue.\n\n"
                "To proceed, either run the installer again and set a MOK "
                "password (to enroll it), or disable Secure Boot in your "
                "firmware first. You are not required to install InterGenOS — "
                "but it will not leave your machine unbootable.",
                "16", "72",
            )
            return None

    # Option C 2026-05-24 — dual-boot detection prompt.
    # YES => GRUB_DISABLE_OS_PROBER=false written permanently to
    #        /etc/default/grub. grub-mkconfig (at install time AND every
    #        kernel update) scans adjacent partitions for other OSes
    #        and adds boot menu entries for Windows / other Linux distros.
    # NO  => GRUB_DISABLE_OS_PROBER=true permanently. os-prober never
    #        runs on this install. Smaller attack surface (os-prober
    #        mounts adjacent partitions during grub-mkconfig, which can
    #        execute filesystem-parsing code on attacker-controlled data).
    # Default YES — most users installing on a machine with another OS
    # want dual-boot to keep working; single-boot users see no behavior
    # difference (os-prober finds nothing, adds nothing).
    detect_other_oses = _ask_yesno(
        "Dual-boot detection",
        "Detect other operating systems (Windows / other Linux distros) on\n"
        "adjacent partitions and add boot menu entries for them?\n\n"
        "YES (recommended for dual-boot installs): GRUB will scan adjacent\n"
        "  partitions on every kernel update and keep dual-boot entries\n"
        "  fresh. Single-boot users: no effect (nothing to detect).\n\n"
        "NO (recommended for single-boot security-hardened installs): GRUB\n"
        "  will never run os-prober. Slightly smaller attack surface (no\n"
        "  filesystem-parsing of adjacent partitions during grub-mkconfig).\n"
        "  If you later add a second OS, you'll need to re-enable os-prober\n"
        "  in /etc/default/grub manually.",
    )

    # D1 / work-plan 1.25: default-boot-target ask. Only fires on an EFI install
    # that already has another installed OS's boot entry (has_other_os_boot_
    # entries() is True) — a single-OS / non-EFI install keeps efibootmgr's
    # prepend with no ask. Default NO (keep the machine's current default; do
    # not silently take over — the D1 user-control concern).
    make_default_boot = _resolve_make_default_boot(
        disks.is_efi(),
        bootloader.has_other_os_boot_entries(),
        lambda: _ask_yesno(
            "Default boot target",
            "Another operating system is installed on this machine.\n\n"
            "Make InterGenOS the DEFAULT boot target?\n\n"
            "YES: InterGenOS boots by default; your other OS stays reachable\n"
            "  from the firmware / GRUB boot menu.\n"
            "NO (recommended): your current default OS keeps booting by\n"
            "  default; pick InterGenOS from the boot menu when you want it.",
            default_no=True,
        ),
    )

    # Wi-Fi carry (2026-07-11): ask only when the live session has an active
    # Wi-Fi connection (netprobe, user-side nmcli). Default YES — carrying
    # the network the user deliberately joined is the expected outcome.
    _wifi_names = netprobe.active_wifi_names()
    carry_wifi = _resolve_carry_wifi(
        _wifi_names,
        lambda: _ask_yesno(
            "Carry Wi-Fi connection",
            "This live session is connected to Wi-Fi"
            f" ({', '.join(_wifi_names or [])}).\n\n"
            "Carry the connection onto the installed system?\n\n"
            "YES (recommended): your machine is already on your Wi-Fi at\n"
            "  first boot — nothing to re-enter.\n"
            "NO: the installed system starts with no saved Wi-Fi networks;\n"
            "  reconnect and re-enter the password after install.",
        ),
    )

    install_io = {
        "disk": disk,
        "root_password": root_pw,
        "username": username,
        "user_password": user_pw,
        "mok_password": mok_pw,
        "detect_other_oses": detect_other_oses,
    }
    # Thread the default-boot choice only when it was actually asked (not None),
    # mirroring state.to_install_io — absent key -> backend keeps the prepend.
    if make_default_boot is not None:
        install_io["make_default_boot"] = make_default_boot
    # Wi-Fi carry: same only-when-asked threading.
    if carry_wifi is not None:
        install_io["carry_wifi"] = carry_wifi
    if luks_enabled:
        install_io["luks_enabled"] = True
        install_io["luks_passphrase"] = luks_passphrase
        if tpm2_enabled:
            install_io["tpm2_enabled"] = True
        if fido2_enabled:
            install_io["fido2_enabled"] = True
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
    """Render the hard-coded integrity warning to stderr.

    Called by integrity.verify_archives() for either decision it can raise: an
    archive whose sha does not match the manifest, and the media that is short
    of archives the manifest promised. The text is chosen and filled in by the
    integrity module — this frontend only prints it, so a decision the module
    learns to raise cannot reach one surface and miss the other.
    """
    from installer.backend.integrity import render_integrity_warning
    print(render_integrity_warning(package_name, expected_sha256,
                                   actual_sha256), file=sys.stderr)


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
    """Return a VerifyConfig for release media, None for an explicitly-marked
    dev/unsigned-test ISO, or raise ReleaseMediaIntegrityError on real release
    media that lacks its trust set (install-integrity §4C — fail-closed).

    The trust DECISION is shared with the GUI + D-Bus mirrors via
    integrity.install_media_trust_decision so all three behave identically.
    The ONLY sanctioned skip is the explicit IGOS_DEV_ALLOW_UNVERIFIED marker
    (written by an UNSIGNED_TEST build), NEVER the mere absence of the manifest
    (red-team R1 — that absence-skip was the dormancy bug). --dry-run skips at
    the caller before this is reached.
    """
    from installer.backend.install import VerifyConfig
    from installer.backend.integrity import install_media_trust_decision
    decision = install_media_trust_decision(
        INSTALL_MEDIA_MANIFEST, INSTALL_MEDIA_PUBKEY,
    )
    if decision == "skip-dev":
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

    _reporter.info(f"Declarative install starting from {yaml_path}")
    _reporter.detail(
        f"locale={cfg['locale']} tz={cfg['timezone']} "
        f"hostname={cfg['hostname']} groups={cfg['package_groups']}"
    )
    _reporter.detail(f"target disk={install_io['disk']}")
    if dry_run:
        _reporter.note("--dry-run set — destructive disk ops will log only.")

    phases_total = len(backend_install.PHASE_ORDER)

    def _progress(phase, current, total, message):
        if phase == backend_install.PHASE_PACKAGES and total != phases_total:
            # per-package fanout from packages.install_packages
            _reporter.detail(f"({current}/{total}) {message}")
            return
        if phase == backend_install.PHASE_HOOKS and total != phases_total:
            # per-hook fanout from hooks.run_post_install_hooks
            _reporter.detail(f"({current}/{total}) {message}")
            return
        # Phase-boundary event. Orchestrator emits two events per phase:
        # enter at current==phase_idx, exit at current==phase_idx+1. A phase
        # that reports trouble surfaces as a warning; an entered phase shows
        # progress tense; a completed phase shows the ✓ verdict.
        phase_idx = backend_install.PHASE_ORDER.index(phase)
        if "WARN" in message or "failed" in message.lower():
            _reporter.warning(f"{phase}: {message}")
        elif current == phase_idx + 1:
            _reporter.ok(f"{phase}: {message}")
        else:
            _reporter.step(phase, message)

    if dry_run:
        verify_config = None
    else:
        from installer.backend.integrity import ReleaseMediaIntegrityError
        try:
            verify_config = _build_verify_config_if_present()
        except ReleaseMediaIntegrityError as e:
            # Fail-closed: real release media missing its trust set. Abort
            # BEFORE run_install (no disk write has happened — PHASE_VERIFY
            # itself runs before partitioning, and we never reach it).
            _reporter.error("Refusing to install — install-media integrity check failed.")
            _reporter.detail(str(e))
            _reporter.detail("No changes were made to the target disk.")
            return 1
    if verify_config is not None:
        _reporter.step("Integrity", "verification armed (signed manifest detected on install media)")
    elif not dry_run:
        _reporter.note("integrity verification skipped — explicit IGOS_DEV_ALLOW_UNVERIFIED dev marker present (unsigned-test media)")

    result = backend_install.run_install(
        yaml_path, install_io,
        str(archive_dir) if archive_dir else None,
        str(packages_dir) if packages_dir else None,
        progress_callback=_progress,
        dry_run=dry_run,
        verify_config=verify_config,
    )

    _reporter.info("")
    if result.success:
        _reporter.ok("Install complete.")
        if result.integrity_overrides_granted:
            _reporter.warning(
                f"{result.integrity_overrides_granted} integrity override(s) granted during install."
            )
            _reporter.detail(f"Review {INTEGRITY_AUDIT_LOG} on the installed system for details.")
        if result.package_fail_count:
            _reporter.note(f"{result.package_fail_count} package(s) failed:")
            for n, msg in result.failed_packages:
                _reporter.detail(f"✗ {n}: {msg}")
        # Venue-aware media wording + the conditional Secure-Boot reminder
        # (same two display conditions as the GUI Done page): the reminder
        # prints ONLY when the machine can take a MOK enrollment at all
        # AND the user set an enrollment password. The retired "(if EFI)
        # follow the MokManager prompts" line promised a prompt that never
        # comes unless Secure Boot is re-enabled in firmware — and promised
        # it on machines that cannot enroll a key at all.
        from installer.backend.disks import live_media_kind
        from installer.backend.secureboot import allows_mok_enrollment
        media_kind = live_media_kind()
        if media_kind == "usb":
            media_txt = "remove the USB install stick"
        elif media_kind == "cdrom":
            media_txt = ("eject the install disc (in a virtual machine, "
                         "detach the ISO from the virtual CD/DVD drive)")
        else:
            media_txt = "remove the install media"
        if install_io.get("mok_password") and allows_mok_enrollment() is True:
            _reporter.detail(f"Reboot, {media_txt}, then re-enable Secure Boot")
            _reporter.detail("in your UEFI firmware setup — that is what triggers")
            _reporter.detail("MokManager, where you enter the MOK enrollment password")
            _reporter.detail("you set to register your machine's signing key.")
            _reporter.detail("See docs/users/secure-boot-and-mok.md.")
        else:
            _reporter.detail(f"Reboot, and {media_txt}.")
        return 0

    if result.integrity_aborted_at:
        _reporter.error(
            f"Install aborted during integrity verification at {result.integrity_aborted_at}."
        )
        _reporter.detail(str(result.error_message))
        _reporter.detail("No changes were made to the target disk.")
        return 1

    _reporter.error(
        f"Install failed at phase {result.phase_completed or '<pre-validation>'}."
    )
    _reporter.detail(str(result.error_message))
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
        _reporter.step("Config", f"install config written to {yaml_path}")

        # Interactive — disk + passwords (Q-TUI-INTERACTIVITY=B)
        install_io = prompt_install_io()
        if install_io is None:
            return _cleanup_on_abort(yaml_path=str(yaml_path))

        # D-010: thread the InterGen AI opt-in choice from walking()
        # answers into install_io. walking() collected it for UX
        # symmetry with the GUI Packages screen; install_io is where
        # the backend PHASE_SERVICES reads it.
        if answers.get("intergen_ai_enable"):
            install_io["intergen_ai_enable"] = True
        # D-019: same install_io threading for SSH server opt-in
        if answers.get("ssh_server_enable"):
            install_io["ssh_server_enable"] = True
            # Optional SSH public key (sshd-password-auth closure
            # Option C 2026-05-22): only forward when non-empty so
            # the backend's absent-equals-no-key-path works.
            if answers.get("ssh_public_key"):
                install_io["ssh_public_key"] = answers["ssh_public_key"]

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
                    _reporter.info("Rebooting…")
                    subprocess.run(["reboot"], check=False)
                else:
                    # Non-systemd init or busybox-only environment: 'reboot'
                    # binary missing. Don't pretend we rebooted.
                    _reporter.warning("'reboot' command not found on this system.")
                    _reporter.detail("Please reboot manually (Ctrl+Alt+Del or your "
                                     "platform's reboot command).")
            else:
                _reporter.note("you can reboot later by running 'reboot' or Ctrl+Alt+Del.")

        return rc

    except KeyboardInterrupt:
        _reporter.info("")
        _reporter.warning("Cancelled by user. No changes were made to the target disk.")
        return 130

    except Exception as e:
        # Last-resort guard. Write the traceback to a debug log (best-effort)
        # so post-incident review has the full stack, but show the user only
        # a sanitized one-liner — they don't need stderr graffiti to know the
        # install failed.
        logged = _write_debug_log(type(e), e)
        _reporter.info("")
        _reporter.error(f"Internal error: {type(e).__name__}: {e}")
        _reporter.detail("Install was aborted; the target disk may be in a partial state.")
        if logged:
            _reporter.detail(f"Full traceback at {DEBUG_LOG_PATH}.")
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

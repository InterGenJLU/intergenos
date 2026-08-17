# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""User account creation for InterGenOS installer.

Refactored 2026-05-27 to use host-side --root invocations instead of
chroot for systemctl + systemd-tmpfiles + chpasswd + passwd + useradd +
groupadd. Rationale: chroot'd systemctl cannot reach a systemd daemon
(none exists in the install chroot — only /proc /sys /dev are mounted,
no /run/systemd) and silently fails on preset-all + enable, defeating
the entire 80/99-preset whitelist mechanism. Host-side invocations with
`--root=<target>` operate on target paths via filesystem-only logic, no
daemon dependency, and surface real exit codes instead of the
2>/dev/null || true blindfold pattern.

No install-time chroot calls remain. xdg-user-dirs setup is delegated
to the xdg-user-dirs package's /etc/xdg/autostart/xdg-user-dirs.desktop
which fires on first GNOME session login (canonical pattern: Arch +
Fedora + Debian + freedesktop spec all rely on the autostart entry).
useradd / groupadd / chpasswd / passwd all use shadow-utils' --root
flag (host-side). systemctl + systemd-tmpfiles use their --root flag.
visudo runs against an absolute target path. chown by numeric uid/gid
looked up from target's /etc/passwd.
"""

import logging
import os
import re
import secrets
import shlex
import subprocess
import termios
from pathlib import Path

from . import trace
from ._validators import validate_password

# C-006: orchestrator (install.py PHASE_VIRTUAL_FS) owns virtual_fs
# lifecycle. set_root_password / create_user / enable_services all run
# between PHASE_VIRTUAL_FS and PHASE_CLEANUP — virtual_fs is already
# mounted into target by the time we get here, do not re-mount.

log = logging.getLogger(__name__)

# The group that may open the Chronicle backup engine's socket
# (/run/chronicle/engine.sock, root:chronicle 0660 — chronicle/api.py
# ENGINE_SOCKET_GROUP). It is an access group: nothing runs as it and it owns
# no files. The account this installer creates is the machine's console user,
# so it is placed in the group here, at the same point every other capability
# group is granted. Without it the backup application opens onto a
# "this account cannot use Chronicle" state on a working system.
#
# The group itself is declared by the intergenos-backup package's
# /usr/lib/sysusers.d/chronicle.conf and created by the systemd-sysusers pass
# in enable_services(); the groupadd loop below is the belt to that braces,
# since useradd -G aborts the whole account creation on a group that is not
# there yet and the two steps are not ordered relative to each other.
CHRONICLE_GROUP = "chronicle"

# The supplementary groups the console account is created with.
DEFAULT_USER_GROUPS = ("wheel", "audio", "video", "cdrom", "input",
                       CHRONICLE_GROUP)

# Anchored regex for the canonical commented `%wheel` line in /etc/sudoers.
# Tolerates arbitrary whitespace between tokens; closes the brittle
# fixed-string-replace that silently no-op'd if upstream sudo shipped with
# tab spacing or extra whitespace, leaving sudo silently disabled for the
# wheel group and locking the user out of administrative recovery.
_SUDOERS_WHEEL_COMMENTED_RE = re.compile(
    r'^#\s*%wheel\s+ALL=\(ALL:ALL\)\s+ALL\s*$',
    re.MULTILINE,
)


def _sha512crypt_hash(password):
    """Pre-hash a password as SHA-512 crypt for `chpasswd -e` consumption.

    chpasswd without flags runs through the PAM password stack
    (pam_chauthtok → pam_unix → pam_pwquality), and pam_pwquality in a
    chroot install context cannot reach its cracklib dictionaries or
    pwquality.conf in a way that makes the password write actually
    succeed — chpasswd silently no-ops, leaving /etc/shadow with the
    useradd `!` placeholder for new users and NO entry at all for root.
    Result on 2026-05-26 install #18: target booted with christopher
    locked (cannot log in) and root with no shadow row.

    Pre-hashing with SHA-512 crypt and passing `chpasswd -e` writes
    directly to /etc/shadow via libcrypt without touching PAM, matching
    the 2026-05-24 `176ac1d8` fix that landed for scripts/create-image.sh
    (live ISO build's intergenos:intergenos credential bootstrap). This
    function carries the same fix to the install pipeline — the install
    surface was the sibling consumer that got missed.

    Runtime password changes via passwd(1) / gnome-control-center / Forge
    continue to enforce the full PAM stack (including pwquality) on the
    installed system — this bypass is install-time-only.
    """
    # Use `openssl passwd -6` (SHA-512 crypt) — matches the 2026-05-24
    # 176ac1d8 fix in scripts/create-image.sh verbatim. Python's stdlib
    # `crypt` module was deprecated in 3.11 and removed in 3.13 (PEP 594);
    # we ship Python 3.14 so `import crypt` would fail. openssl is
    # universally available on the host (live ISO ships it for the TLS
    # chain) and on every chroot context this code runs in.
    #
    # -6 = SHA-512. -salt accepts the salt directly. Stdin = password.
    # Captured + decoded; output is the full $6$<salt>$<hash> string
    # that chpasswd -e expects.
    salt = secrets.token_urlsafe(16)
    result = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", salt, "-stdin"],
        input=password, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@trace.trace_install_step("set_root_password")
def set_root_password(target, password):
    """Set the root password on the target system.

    Pre-hash + chpasswd --root to bypass install-time PAM (see _sha512crypt_hash).
    Host-side --root invocations (was chroot) so systemctl-class silent-fail
    pattern doesn't apply and we get real exit codes.
    """
    err = validate_password(password, role="root password")
    if err:
        raise ValueError(err)
    # Pre-hash + feed via stdin: avoids both PAM (which fails in chroot)
    # AND process table exposure of the plaintext.
    hashed = _sha512crypt_hash(password)
    target_str = str(target)

    # chpasswd --root <target> -e: write /etc/shadow on target via libcrypt,
    # no PAM, no chroot needed.
    cmd = ["chpasswd", "--root", target_str, "-e"]
    result = trace.traced_run(
        cmd, input=f"root:{hashed}\n",
        phase="users",
        intent="set root password (pre-hashed SHA-512crypt)",
    )
    if result.returncode != 0:
        raise trace.install_failure(
            where="users.py:set_root_password / chpasswd --root -e",
            why="root password not written; installed system has no root "
                "credentials and is unrecoverable from emergency shell.",
            cmd=cmd, rc=result.returncode,
            stdout=result.stdout, stderr=result.stderr,
        )

    # passwd --root --maxdays 99999: disable password expiry for initial setup.
    result = trace.traced_run(
        ["passwd", "--root", target_str, "--maxdays", "99999", "root"],
        phase="users",
        intent="disable root password expiry",
    )
    if result.returncode != 0:
        log.warning(
            "passwd --root --maxdays 99999 root returned rc=%d; root may "
            "have a default expiry. stdout=%r stderr=%r",
            result.returncode, result.stdout, result.stderr,
        )


@trace.trace_install_step("create_user")
def create_user(target, username, password, groups=None):
    """Create a user account on the target system.

    Args:
        target: target root path
        username: login name
        password: password (plain text — chpasswd handles hashing)
        groups: list of supplementary groups (default: DEFAULT_USER_GROUPS)
    """
    if groups is None:
        groups = list(DEFAULT_USER_GROUPS)
    elif CHRONICLE_GROUP not in groups:
        # An explicit list is the caller's choice and is not overridden, but it
        # is not allowed to be silent: without this group the account cannot
        # open the backup engine's socket, so Chronicle's window opens onto a
        # "not allowed" state with no way for the user to know why.
        log.warning(
            "explicit group list for %s omits %r; the account will not be "
            "able to use the Chronicle backup application (its engine socket "
            "is mode 0660, group %s)",
            username, CHRONICLE_GROUP, CHRONICLE_GROUP,
        )

    err = validate_password(password, role="user password")
    if err:
        raise ValueError(err)

    target_str = str(target)

    # Ensure every supplementary group exists in the target before
    # useradd -G runs — useradd fails the whole user-creation if ANY
    # named group is missing. Surfaced 2026-05-26 install attempt #8:
    # audio/video/cdrom/input groups were absent on a fresh-pkm-install
    # target because they live in /etc/group, which no package ships
    # (created at build time by chroot-setup.sh — same shape as the
    # /lib64 dynamic-linker symlinks gap landed in 0abd0de3). The
    # `-r` flag creates a system group (GID < 1000); idempotent
    # via the getent guard so a future package that DOES ship these
    # groups composes cleanly.
    #
    # Host-side groupadd --root + getent --root (was chroot). getent
    # has no --root flag; check /etc/group directly via Python instead.
    target_group_file = Path(target) / "etc/group"
    existing_groups = set()
    if target_group_file.exists():
        for line in target_group_file.read_text().splitlines():
            if ":" in line:
                existing_groups.add(line.split(":", 1)[0])
    for grp in groups:
        if grp in existing_groups:
            continue
        cmd = ["groupadd", "--root", target_str, "--system", grp]
        result = trace.traced_run(
            cmd, phase="users",
            intent=f"create supplementary group {grp}",
        )
        if result.returncode != 0:
            raise trace.install_failure(
                where=f"users.py:create_user / groupadd --root --system {grp}",
                why=(f"useradd -G with a missing group aborts the whole "
                     f"user creation. Without {grp}, the first user can't be "
                     f"created and the installed system has no admin login."),
                cmd=cmd, rc=result.returncode,
                stdout=result.stdout, stderr=result.stderr,
            )

    # Create user with home directory via host-side useradd --root.
    group_str = ",".join(groups)
    cmd = ["useradd", "--root", target_str,
           "-m", "-G", group_str, "-s", "/bin/bash", username]
    result = trace.traced_run(
        cmd, phase="users",
        intent=f"create user {username}",
    )
    if result.returncode != 0 and "already exists" not in (result.stderr or ""):
        raise trace.install_failure(
            where=f"users.py:create_user / useradd --root {username}",
            why=("first user not created; installed system has no admin login. "
                 "Cannot recover from emergency shell without root password."),
            cmd=cmd, rc=result.returncode,
            stdout=result.stdout, stderr=result.stderr,
        )

    # Pre-hash + chpasswd --root -e: bypass install-time PAM, avoid process
    # table plaintext exposure. See _sha512crypt_hash() docstring.
    hashed = _sha512crypt_hash(password)
    cmd = ["chpasswd", "--root", target_str, "-e"]
    result = trace.traced_run(
        cmd, input=f"{username}:{hashed}\n",
        phase="users",
        intent=f"set password for {username} (pre-hashed SHA-512crypt)",
    )
    if result.returncode != 0:
        raise trace.install_failure(
            where=f"users.py:create_user / chpasswd --root -e (user={username})",
            why=(f"{username}'s password not written; first user cannot log "
                 "in. Installed system unusable from console + GDM."),
            cmd=cmd, rc=result.returncode,
            stdout=result.stdout, stderr=result.stderr,
        )

    # XDG user directories (Desktop, Documents, Downloads, etc.) are
    # NOT created here. The xdg-user-dirs package ships
    # /etc/xdg/autostart/xdg-user-dirs.desktop which fires on first
    # GNOME login and runs xdg-user-dirs-update in the proper user-
    # session context (real $HOME, real $DBUS_SESSION_BUS_ADDRESS,
    # locale-aware translation, logind-registered session). This is
    # the canonical pattern every other distro uses (Arch, Fedora,
    # Debian per their respective docs).
    #
    # Earlier code here ran `chroot ... su - <user> -c xdg-user-dirs-
    # update` at install time, which failed with pam_start: error 26
    # because the install chroot has no /run/dbus + no /run/user/<uid>
    # for PAM session setup. Deleting the install-time call removes a
    # PAM-in-chroot anti-pattern + relies on the upstream package's
    # autostart entry instead — decided 2026-05-27 (Option A
    # post-install-#25 trace surfaced the pam_start: error 26).

    # Enable sudo for wheel group (if sudoers exists). Stage to
    # /etc/sudoers.new + run visudo -c -f for syntax-check before
    # committing — a malformed sudoers locks the user out of sudo
    # entirely. If verification fails, leave sudoers unchanged and
    # log a warning rather than fail the install (user can still
    # gain root via initial password and hand-edit sudoers).
    #
    # visudo has no --root flag (its --file flag takes an absolute path
    # but visudo treats it as relative to the chroot if invoked there).
    # Easiest: invoke visudo from host, pointing at the staging file's
    # host-side absolute path. visudo doesn't care about target context
    # for syntax-checking (it's a pure parser run).
    sudoers = Path(target) / "etc" / "sudoers"
    if sudoers.exists():
        content = sudoers.read_text()
        new_content = _SUDOERS_WHEEL_COMMENTED_RE.sub(
            '%wheel ALL=(ALL:ALL) ALL', content
        )
        if new_content != content:
            staging = Path(target) / "etc" / "sudoers.new"
            staging.write_text(new_content)
            result = trace.traced_run(
                ["visudo", "-c", "-f", str(staging)],
                phase="users",
                intent="syntax-check staged sudoers before commit",
            )
            if result.returncode == 0:
                staging.replace(sudoers)
                trace.trace_event("sudoers_wheel_enabled",
                                  path=str(sudoers))
            else:
                staging.unlink(missing_ok=True)
                log.warning(
                    "sudoers regex-sed produced syntactically-invalid "
                    "file (visudo: %s); leaving sudoers unchanged",
                    (result.stderr or "").strip(),
                )


@trace.trace_install_step("enable_services")
def enable_services(target):
    """Enable essential systemd services on the target.

    sshd.service is intentionally NOT in this list — per D-019 (amends
    D-007 sshd-default arm), SSH is opt-in via the Forge UI. The
    PHASE_SERVICES caller in install.py conditionally enables sshd
    based on install_io.get("ssh_server_enable") + adds the firewall
    rule via enable_ssh_server() below.
    """
    target_str = str(target)

    # Apply distribution preset policy BEFORE explicit enables.
    # Fedora-style pattern (research dossier private repo 06c930e):
    # 80-intergenos-enable.preset whitelists what we ship enabled,
    # 99-intergenos-default-disable.preset catches everything else.
    # Without this step every service-shipping package's
    # WantedBy=multi-user.target auto-enables — 40+ servers
    # (httpd/nginx/postgres/mariadb/...) fire on first boot. A
    # security-only-alignment violation. Preset files owned by intergenos-base-files package
    # (lands first via INSTALL_ORDER_ESSENTIALS). Plan v2 Change 4
    # (2026-05-27, bilateral review APPROVE-clean at 06:41Z).
    #
    # Refactor 2026-05-27: host-side `systemctl --root=<target>` instead
    # of `chroot ... systemctl`. Chroot lacks /run/systemd so systemctl
    # cannot reach a running daemon and silently fails — defeating the
    # entire preset whitelist. --root operates on filesystem paths only,
    # no daemon dependency. Real exit code surfaced (no 2>/dev/null
    # blindfold) so a future regression is observable.
    cmd = ["systemctl", "--root", target_str, "preset-all"]
    result = trace.traced_run(
        cmd, phase="services",
        intent="apply 80/99-intergenos preset policy (whitelist on, default disable)",
    )
    if result.returncode != 0:
        raise trace.install_failure(
            where="users.py:enable_services / systemctl --root preset-all",
            why=("preset-all defeats the 80/99-intergenos whitelist; without it "
                 "every WantedBy=multi-user.target server-shipping package auto-"
                 "enables on first boot (httpd/nginx/postgres/mariadb/lighttpd/"
                 "...). Security-only-alignment violation — install must not proceed."),
            cmd=cmd, rc=result.returncode,
            stdout=result.stdout, stderr=result.stderr,
        )

    # Materialize /var/log/{btmp,lastlog,faillog,wtmp} declaratively
    # from intergenos-base-files /usr/lib/tmpfiles.d/00-intergenos.conf
    # (Block D login-accounting init). PHASE_SERVICES is single
    # canonical invocation point per plan v2 (R5 option-b).
    #
    # Host-side --root invocation for the same reason as preset-all:
    # systemd-tmpfiles in chroot tries to talk to a running journald
    # for status reports and produces confusing errors that don't
    # actually prevent the filesystem work but conflate fatal-vs-warning.
    # --root variant is the canonical offline invocation.
    # Run systemd-sysusers BEFORE systemd-tmpfiles. tmpfiles.d entries
    # commonly chown to package-shipped system users (colord, polkitd,
    # avahi, lp, etc.) — those users must exist in /etc/passwd at the
    # moment tmpfiles --create runs or it reports "Failed to resolve
    # user 'X'" and exits rc=65 (EX_DATAERR). systemd-sysusers reads
    # /usr/lib/sysusers.d/*.conf and creates the declared users on the
    # target via host-side --root (same family as systemctl --root +
    # systemd-tmpfiles --root). intergenos-base-files ships a cross-
    # cutting 00-intergenos-extras.conf safety net for packages whose
    # build.sh creates the user in configure() at BUILD TIME (wrong
    # layer — user never reaches install target). Surfaced 2026-05-27
    # install attempt #25 trace.
    cmd = ["systemd-sysusers", "--root", target_str]
    result = trace.traced_run(
        cmd, phase="services",
        intent="create declarative system users from /usr/lib/sysusers.d/*.conf",
    )
    if result.returncode != 0:
        log.warning(
            "systemd-sysusers --root returned rc=%d; some declarative "
            "system users may NOT have been created. systemd-tmpfiles "
            "will likely produce 'Failed to resolve user' errors for "
            "those. stderr=%r", result.returncode, result.stderr,
        )

    cmd = ["systemd-tmpfiles", "--root", target_str, "--create"]
    result = trace.traced_run(
        cmd, phase="services",
        intent="materialize /var/log/{btmp,lastlog,faillog,wtmp} per 00-intergenos.conf",
    )
    # systemd-tmpfiles returns rc=65 (EX_DATAERR) on ANY entry failure
    # across ALL shipped tmpfiles.d/*.conf files. That includes "Unknown
    # user 'colord'" or "Unknown group 'polkitd'" from third-party
    # package tmpfiles configs whose users were not yet created by their
    # own post_install hooks — NOT a fatal condition for our login-
    # accounting needs. The CRITICAL outcome is that /var/log/btmp +
    # lastlog + wtmp + faillog exist (those are what pam_lastlog +
    # pam_tally2 + pam_faillock open at login time). Check those
    # explicitly. install_failure only if the CRITICAL files are absent.
    if result.returncode != 0:
        log.warning(
            "systemd-tmpfiles --root --create returned rc=%d (likely "
            "third-party tmpfiles.d/*.conf entries failed; not necessarily "
            "fatal). stderr=%r", result.returncode, result.stderr,
        )
    critical_login_files = [
        Path(target) / "var/log/btmp",
        Path(target) / "var/log/lastlog",
        Path(target) / "var/log/wtmp",
        Path(target) / "var/log/faillog",
    ]
    missing_critical = [str(p) for p in critical_login_files if not p.exists()]
    if missing_critical:
        raise trace.install_failure(
            where="users.py:enable_services / post-systemd-tmpfiles file check",
            why=("login accounting files NOT created despite tmpfiles run. "
                 "PAM session handlers (pam_lastlog + pam_tally2 + pam_faillock) "
                 "will fail with cryptic 'cannot open' errors on every login. "
                 "Block-D login-accounting init failed."),
            cmd=cmd, rc=result.returncode,
            stdout=result.stdout, stderr=result.stderr,
            extra={"missing_files": missing_critical},
        )
    trace.trace_event("login_accounting_files_verified",
                      files=[str(p) for p in critical_login_files])

    # systemd-resolved provides DNS (NetworkManager uses it as its resolver
    # backend). systemd-networkd is deliberately NOT enabled here: NetworkManager
    # owns networking on InterGenOS (80-intergenos-enable.preset + the preset-all
    # above disable networkd). The previous explicit enable of networkd ran AFTER
    # preset-all and re-enabled it, so the Forge-installed system ran networkd
    # alongside NetworkManager → duplicate IPv4 + duplicate default routes on the
    # NIC (GBC001 eval residual, 2026-06-06). The live/qcow2 path already excluded
    # networkd (GBC001.5 fix #4); dropping it here aligns the installed path.
    services = [
        "systemd-resolved.service",
    ]
    for svc in services:
        result = trace.traced_run(
            ["systemctl", "--root", target_str, "enable", svc],
            phase="services",
            intent=f"explicit enable {svc}",
        )
        if result.returncode != 0:
            log.warning(
                "systemctl --root enable %s returned rc=%d; service NOT "
                "enabled. stdout=%r stderr=%r",
                svc, result.returncode, result.stdout, result.stderr,
            )

    # Enable serial console for VM/server use — but ONLY if ttyS0 is actually
    # functional. GBC002.4 (2026-06-08): on bare-metal laptops the serial port
    # is often present-but-dead (e.g. the HP A12), so agetty's TCGETS ioctl
    # fails and systemd respawns serial-getty@ttyS0 every ~10s — a journal-
    # spamming loop with no benefit (90+ errors on the GBC002 A12 install). The
    # installer runs on the live ISO on the SAME hardware, so we probe /dev/ttyS0
    # here with the exact TCGETS agetty needs: VMs + real serial ports pass and
    # keep the console; dead bare-metal ports are skipped. (Host-side os.symlink:
    # symlink creation needs no chroot context, just the right target path.)
    serial_ok = False
    try:
        _fd = os.open("/dev/ttyS0", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            termios.tcgetattr(_fd)
            serial_ok = True
        finally:
            os.close(_fd)
    except (OSError, termios.error):
        # termios.error is NOT a subclass of OSError. On hardware with a
        # PHANTOM /dev/ttyS0 (HP A12: the node exists and os.open() succeeds,
        # but the TCGETS ioctl returns EIO) tcgetattr raises termios.error(5,
        # 'Input/output error') — which an `except OSError` misses, so it
        # escaped and killed enable_services mid-install ("error: (5, 'Input/
        # output error')"). Treat a TCGETS failure the same as an open
        # failure: the port is non-functional, so skip serial-getty.
        serial_ok = False

    if serial_ok:
        serial_link = Path(target) / "etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service"
        serial_target = "/usr/lib/systemd/system/serial-getty@.service"
        try:
            serial_link.parent.mkdir(parents=True, exist_ok=True)
            if serial_link.exists() or serial_link.is_symlink():
                serial_link.unlink()
            os.symlink(serial_target, serial_link)
            trace.trace_event("serial_getty_symlink",
                              path=str(serial_link), target=serial_target)
        except Exception as exc:
            log.warning("serial-getty@ttyS0 symlink creation failed: %s", exc)
    else:
        log.info("serial-getty@ttyS0 not enabled: /dev/ttyS0 absent or "
                 "non-functional (avoids the bare-metal agetty respawn loop)")
        trace.trace_event("serial_getty_skipped",
                          reason="ttyS0 absent or TCGETS-failed")


@trace.trace_install_step("enable_greeter_monitor_sync")
def enable_greeter_monitor_sync(target, username):
    """Enable the greeter monitor-layout sync for the primary user.

    The gdm package ships igos-greeter-monitors-sync@.path/.service — a
    templated watch on /home/<user>/.config/monitors.xml that mirrors the
    layout into /var/lib/gdm/seat0/config/monitors.xml with uid-drift-proof
    modes (file 0644, dirs 0755), so the greeter renders the user's real
    monitor arrangement instead of mutter's clone-all fallback (decided
    2026-07-21). A preset cannot carry a templated instance whose instance
    name is only known at install time, so the enable happens HERE, where
    the wizard-created username is in hand.

    Host-side `systemctl --root` for the same no-daemon-in-chroot reason as
    preset-all above. Fail-loud: on our media the unit is always shipped by
    the gdm package, so a failed enable means a corrupted or mismatched
    payload — the install must not proceed silently degraded.
    """
    target_str = str(target)
    instance = f"igos-greeter-monitors-sync@{username}.path"
    cmd = ["systemctl", "--root", target_str, "enable", instance]
    result = trace.traced_run(
        cmd, phase="services",
        intent=f"enable greeter monitor-layout sync for primary user {username}",
    )
    if result.returncode != 0:
        raise trace.install_failure(
            where="users.py:enable_greeter_monitor_sync / systemctl --root enable",
            why=(f"enabling {instance} failed; the gdm package ships the unit "
                 f"on every InterGenOS medium, so a failed enable indicates a "
                 f"corrupted or mismatched payload — refusing to ship a "
                 f"silently-degraded greeter sync."),
            cmd=cmd, rc=result.returncode,
            stdout=result.stdout, stderr=result.stderr,
        )


def enable_bootorder_check(target):
    """Enable the boot-order checker on the target.

    The forge package ships intergenos-bootorder-check.service, which at
    every boot compares the UEFI boot order against the entry the installer
    registered and restores that entry to the front when the install recorded
    InterGenOS as the default boot target. A firmware that enumerates the
    ESP's fallback loader as its own boot option was measured placing that
    option first, so the machine booted through \\EFI\\BOOT\\BOOTX64.EFI
    rather than the registered entry.

    Enabled HERE rather than by a preset line: the unit belongs to forge, and
    the preset policy lives in intergenos-base-files — enabling it at this
    point also puts it after enable_services()'s `systemctl preset-all`,
    whose 99- catch-all `disable *` would otherwise revert it.

    Host-side `systemctl --root` for the same no-daemon-in-chroot reason as
    preset-all. Fail-loud: the unit ships on every InterGenOS medium, so a
    failed enable means a corrupted or mismatched payload.
    """
    target_str = str(target)
    unit = "intergenos-bootorder-check.service"
    cmd = ["systemctl", "--root", target_str, "enable", unit]
    result = trace.traced_run(
        cmd, phase="services",
        intent="enable the boot-order checker so firmware that reorders "
               "NVRAM cannot silently leave the machine booting the "
               "removable-media fallback loader",
    )
    if result.returncode != 0:
        raise trace.install_failure(
            where="users.py:enable_bootorder_check / systemctl --root enable",
            why=(f"enabling {unit} failed; the forge package ships the unit "
                 f"on every InterGenOS medium, so a failed enable indicates a "
                 f"corrupted or mismatched payload — refusing to ship a "
                 f"system whose boot entry is never checked."),
            cmd=cmd, rc=result.returncode,
            stdout=result.stdout, stderr=result.stderr,
        )


@trace.trace_install_step("seed_greeter_monitor_layout")
def seed_greeter_monitor_layout(target):
    """Stage + install the pre-configuration greeter monitor layout.

    Before any user layout exists, the target's first greeter renders
    mutter's clone-all fallback — stretched across every monitor at default
    scale on a multi-head box. The live install session's own compositor
    state is the honest per-machine source: the PRIMARY logical monitor is
    the screen the installing user is looking at, at its real mode and
    scale. This reads that state (display.read_live_display_state) and
    writes the synthesized single-primary layout twice on the target:

      - /var/lib/gdm/seat0/config/monitors.xml — the direct delivery, so
        the very first greeter can render single-monitor;
      - /var/lib/igos/greeter-monitors-seed.xml — the staged seed the
        gdm-shipped igos-greeter-monitors-seed.service (condition-gated,
        Before=gdm.service) re-installs on any boot where the target file
        is absent. GDM's first-boot greeter init has been observed to wipe
        the seat-state dir, so install-time-only delivery is not durable;
        the unit makes it self-healing by the next boot.

    Modes per the sync-helper doctrine: dirs 0755, files 0644 — GDM chowns
    seat state to a per-boot DynamicUser, so mode bits carry readability.

    BEST-EFFORT BY DESIGN, unlike enable_greeter_monitor_sync above: the
    seed depends on live-session runtime state (a session bus, a
    compositor) that a headless or serial-TUI install legitimately lacks.
    A skip is traced with its reason and degrades to the status-quo
    fallback greeter — never to a failed install. Returns True when
    seeded, False when skipped.
    """
    from . import display
    try:
        state = display.read_live_display_state()
        xml = display.synthesize_primary_only_layout(state)
    except display.DisplayStateError as exc:
        log.warning("greeter monitor seed skipped: %s", exc)
        trace.trace_event("greeter_monitor_seed_skipped", reason=str(exc))
        return False

    target = Path(target)
    staged = target / "var/lib/igos/greeter-monitors-seed.xml"
    seat_config = target / "var/lib/gdm/seat0/config"
    seeded = seat_config / "monitors.xml"

    # /var/lib/igos and /var/lib/gdm are package-owned on the target — only
    # ensure existence there. The seat0 chain is ours to create; pin the
    # traverse-bearing 0755 explicitly (the F31 mode-bit doctrine).
    staged.parent.mkdir(parents=True, exist_ok=True)
    for d in (seat_config.parent, seat_config):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o755)
    for f in (staged, seeded):
        f.write_text(xml)
        os.chmod(f, 0o644)
    trace.trace_event(
        "greeter_monitor_seed",
        staged=str(staged), seeded=str(seeded), size=len(xml))
    return True


@trace.trace_install_step("seed_user_monitor_layout")
def seed_user_monitor_layout(target, username):
    """Seed the created user's ~/.config/monitors.xml from the live session.

    The greeter seed above closes the multi-head race for GDM, but the
    USER's first login was left unseeded: with no stored monitor
    configuration, mutter settles the connected-monitor set while the
    session's first background paint and offscreen effects run against
    unsettled state. On a multi-GPU box that race lost — the desktop
    rendered the solid primary-color fallback on every screen and windows
    were thrown onto a wrong default primary until a monitor-configuration
    re-apply forced a repaint (measured on a triple-GPU install,
    2026-07-31: 233 offscreen-framebuffer failures in the first login's
    journal; zero on every later boot, because the recovery wrote
    monitors.xml and subsequent logins apply a stored configuration before
    painting). Seeding the same synthesized single-primary layout the
    greeter gets makes the FIRST login take the settled path too, and
    keeps the user and greeter layouts consistent until the user saves
    their own (at which point the r76 sync service mirrors it back to the
    greeter).

    Same best-effort posture as the greeter seed: unreadable live state
    traces a skip, never fails the install. Ownership is resolved from the
    target's /etc/passwd (the created user is not in the host's passwd);
    an unresolvable user traces a skip and writes nothing — a root-owned
    monitors.xml would be worse than none (mutter could read but never
    update it).
    """
    from . import display
    try:
        state = display.read_live_display_state()
        xml = display.synthesize_primary_only_layout(state)
    except display.DisplayStateError as exc:
        log.warning("user monitor seed skipped: %s", exc)
        trace.trace_event("user_monitor_seed_skipped",
                          username=username, reason=str(exc))
        return False

    target = Path(target)
    target_passwd = target / "etc/passwd"
    uid = gid = None
    if target_passwd.exists():
        for line in target_passwd.read_text().splitlines():
            parts = line.split(":")
            if len(parts) >= 4 and parts[0] == username:
                uid, gid = int(parts[2]), int(parts[3])
                break
    if uid is None:
        log.warning(
            "seed_user_monitor_layout: could not resolve %s uid/gid from "
            "target /etc/passwd; seed skipped (a root-owned monitors.xml "
            "would block the user's own layout updates)", username)
        trace.trace_event("user_monitor_seed_skipped",
                          username=username,
                          reason="user_not_in_target_passwd")
        return False

    config_dir = target / "home" / username / ".config"
    seeded = config_dir / "monitors.xml"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)
    seeded.write_text(xml)
    os.chmod(seeded, 0o644)
    for p in (config_dir, seeded):
        os.chown(p, uid, gid)
    trace.trace_event(
        "user_monitor_seed",
        username=username, uid=uid, gid=gid,
        seeded=str(seeded), size=len(xml))
    return True


def enable_ssh_server(target, username=None, public_key=None):
    """Enable sshd.service AND open TCP/22 in /etc/nftables.conf.

    Called by PHASE_SERVICES only when install_io["ssh_server_enable"]
    is True (D-019 opt-in path). Without the firewall rule, the
    opt-in would leave sshd listening locally but blocked from the
    network by the D-011 default-deny posture — defeating user intent.
    Both arms run together so YES means "actually-usable SSH server".

    Optional decided 2026-05-22 Option C (sshd-password-auth
    closure): when `public_key` is provided AND `username` is set,
    write /home/<username>/.ssh/authorized_keys (0600; .ssh 0700;
    owned by user) AND drop /etc/ssh/sshd_config.d/02-intergenos-keys-
    only.conf disabling password authentication. When `public_key` is
    empty, password SSH login stays on (upstream default + Mozilla
    Modern hardening from the 01-intergenos-hardening.conf drop-in).
    """
    result = trace.traced_run(
        ["systemctl", "--root", str(target), "enable", "sshd.service"],
        phase="services",
        intent="enable sshd.service (D-019 SSH server opt-in)",
    )
    if result.returncode != 0:
        log.warning(
            "systemctl --root enable sshd.service returned rc=%d; sshd "
            "NOT enabled despite user opt-in. stdout=%r stderr=%r",
            result.returncode, result.stdout, result.stderr,
        )

    # Insert the TCP/22 accept rule into /etc/nftables.conf just above
    # the "Everything else inbound DROPS" comment marker. The marker
    # is a stable structural feature of the intergenos-firewall-defaults
    # ruleset; insertion (vs. file rewrite) lets future ruleset updates
    # from the package compose cleanly. Direct file I/O on the target's
    # mounted filesystem is more robust than sed-via-chroot quoting.
    nft_conf = Path(target) / "etc" / "nftables.conf"
    if nft_conf.exists():
        sshd_rule_block = (
            "        # SSH server (opt-in via Forge install per D-019)\n"
            "        tcp dport 22 accept\n\n"
        )
        marker = "        # Everything else inbound DROPS"
        contents = nft_conf.read_text()

        if "tcp dport 22 accept" not in contents and marker in contents:
            new_contents = contents.replace(
                marker, sshd_rule_block + marker, 1)
            nft_conf.write_text(new_contents)
        elif marker not in contents and "tcp dport 22 accept" not in contents:
            log.warning(
                "enable_ssh_server: marker '%s' not found in "
                "nftables.conf; skipping insertion. User can manually "
                "open TCP/22.", marker
            )
    else:
        log.warning(
            "enable_ssh_server: /etc/nftables.conf missing in target; "
            "skipping firewall rule insertion. The sshd opt-in still "
            "enabled the service; user can manually open TCP/22."
        )

    # Optional public-key install + keys-only sshd_config drop-in
    # (decided 2026-05-22 Option C). When the user pasted a
    # public key in the Forge UI, install it for the new user and
    # disable password authentication.
    if public_key and username:
        _install_ssh_authorized_key(target, username, public_key)
        _ship_ssh_keys_only_dropin(target)


def _install_ssh_authorized_key(target, username, public_key):
    """Write /home/<username>/.ssh/authorized_keys with the given key.

    Permissions: ~/.ssh = 0700, authorized_keys = 0600, both owned by
    the user. sshd refuses to use authorized_keys with looser perms.
    Resolves username -> uid/gid via chroot lookup so the new user's
    actual ownership is honored (rather than guessing the Forge-created
    user has UID 1000).
    """
    ssh_dir = Path(target) / "home" / username / ".ssh"
    auth_keys = ssh_dir / "authorized_keys"

    ssh_dir.mkdir(parents=True, exist_ok=True)
    auth_keys.write_text(public_key.rstrip() + "\n")

    ssh_dir.chmod(0o700)
    auth_keys.chmod(0o600)

    # Set ownership. Look up UID/GID from target's /etc/passwd directly
    # (host's getent can't resolve target's users; chroot avoidance
    # consistent with the rest of the refactor). Then host-side chown
    # by numeric ID.
    target_passwd = Path(target) / "etc/passwd"
    uid = gid = None
    if target_passwd.exists():
        for line in target_passwd.read_text().splitlines():
            parts = line.split(":")
            if len(parts) >= 4 and parts[0] == username:
                uid, gid = int(parts[2]), int(parts[3])
                break
    if uid is None:
        log.warning(
            "_install_ssh_authorized_key: could not resolve %s uid/gid "
            "from target /etc/passwd; .ssh ownership stays root:root "
            "(sshd will reject this — user needs to chown manually)",
            username,
        )
        trace.trace_event("ssh_authkeys_chown_skipped",
                          username=username, reason="user_not_in_target_passwd")
        return
    # Recursive chown via os.walk + os.chown (host-side, no chroot).
    for root, dirs, files in os.walk(ssh_dir):
        for d in dirs:
            os.chown(Path(root) / d, uid, gid)
        for f in files:
            os.chown(Path(root) / f, uid, gid)
    os.chown(ssh_dir, uid, gid)
    trace.trace_event("ssh_authkeys_installed",
                      username=username, uid=uid, gid=gid,
                      path=str(auth_keys))


def _ship_ssh_keys_only_dropin(target):
    """Drop /etc/ssh/sshd_config.d/02-intergenos-keys-only.conf.

    Composes with 00-intergenos-d007.conf (PermitRootLogin no) and
    01-intergenos-hardening.conf (Mozilla Modern). The 02- drop-in
    disables PasswordAuthentication + ChallengeResponseAuthentication
    + KbdInteractiveAuthentication and sets PubkeyAuthentication yes
    explicit. Drop-in numbering 02- ensures load order after the
    hardening profile so this is the last writer.
    """
    dropin_dir = Path(target) / "etc" / "ssh" / "sshd_config.d"
    dropin_path = dropin_dir / "02-intergenos-keys-only.conf"

    dropin_dir.mkdir(parents=True, exist_ok=True)
    dropin_path.write_text(
        "# InterGenOS -- key-based SSH authentication only\n"
        "# Shipped by the installer when the user provided an SSH\n"
        "# public key during the Forge install (decided\n"
        "# 2026-05-22 Option C; sshd-password-auth audit closure).\n"
        "#\n"
        "# Composes with 00-intergenos-d007.conf (PermitRootLogin no)\n"
        "# and 01-intergenos-hardening.conf (Mozilla Modern). The\n"
        "# 02- prefix puts this drop-in last in sshd_config.d/ load\n"
        "# order so it overrides any earlier permissive setting.\n"
        "#\n"
        "# To revert (and allow password SSH login again):\n"
        "#   rm /etc/ssh/sshd_config.d/02-intergenos-keys-only.conf\n"
        "#   systemctl reload sshd.service\n"
        "\n"
        "PasswordAuthentication no\n"
        "ChallengeResponseAuthentication no\n"
        "KbdInteractiveAuthentication no\n"
        "PubkeyAuthentication yes\n"
    )
    dropin_path.chmod(0o644)


def remove_test_accounts(target):
    """Defense-in-depth: guarantee no LFS test account leaks onto the installed
    system.

    The LFS Ch8 test suite runs as a `tester` account. shadow's post_install no
    longer creates it (the `useradd` was removed) and the live ISO root is
    scrubbed pre-squashfs, but a stray tester:1001 + /home/tester leaking onto a
    shipped system bit us once (GBC001.5 install). So we remove it here too,
    regardless of which archive post-install hooks ran on the target — userdel
    --root any test account present, then re-check. The installed system is
    archive-based (pkm), so this is the install-side counterpart to the
    build-squashfs live-ISO scrub.

    Best-effort + verified: returns {"removed": [...], "survivors": [...]} so the
    caller can warn on a survivor, but a leftover does not by itself abort an
    otherwise-complete install.
    """
    target_str = str(target)
    passwd = os.path.join(target_str, "etc", "passwd")
    test_accounts = ("tester",)

    def _present():
        try:
            with open(passwd) as fh:
                names = {ln.split(":", 1)[0] for ln in fh if ":" in ln}
        except OSError:
            return set()
        return {a for a in test_accounts if a in names}

    removed = []
    for acct in sorted(_present()):
        trace.traced_run(
            ["userdel", "--root", target_str, "-r", acct],
            phase="cleanup",
            intent=f"scrub stray LFS test account: {acct}",
        )
        removed.append(acct)
    return {"removed": removed, "survivors": sorted(_present())}

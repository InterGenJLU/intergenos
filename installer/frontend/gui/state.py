# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Central installer state passed between screens.

One dataclass instance flows through the NavigationView. Each screen reads
the fields it cares about on entry and writes back on next/confirm.

Why a dataclass and not (say) a Gtk.Stack-shared dict: dataclasses give us
type hints + IDE autocomplete + a single audit point for what an install
actually requires. Adding a field is one line in this file; forgetting to
populate it surfaces as an attribute error rather than a silent KeyError.

Phase 6 additions: yaml-builder methods (`build_install_yaml`,
`write_install_yaml`, `to_install_io`, `to_run_install_kwargs`) so the
ProgressPage can hand the collected state to the Phase 4 backend
orchestrator (`installer.backend.install.run_install`) without each screen
needing to know the orchestrator's interface shape.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


YAML_SCHEMA_VERSION = 1
DEFAULT_YAML_PATH = "/var/lib/forge/install.yaml"


@dataclass
class InstallerState:
    """Mutable state shared across all 9 screens.

    Defaults match what the TUI walking sequence proposes (en_US.UTF-8 / UTC /
    intergenos hostname / core+base+desktop-gnome groups). The GUI screens
    surface those defaults as pre-filled values so the user can hit "next"
    through screens they don't care about.
    """

    # --- Welcome screen (no state captured; just acknowledged) ---
    welcome_acked: bool = False

    # --- Keyboard / Locale / Timezone screen ---
    keymap: str = "us"
    locale: str = "en_US.UTF-8"
    timezone: str = "UTC"

    # --- Disk screen ---
    # v1 ships fresh-install only. Backend has alongside-install primitives
    # (disks.partition_disk_alongside, detect_shrinkable_ntfs, shrink_ntfs)
    # kept for future wiring once the alongside UX/recovery story is
    # designed.
    target_disk: Optional[str] = None
    confirm_destructive: bool = False

    # --- LUKS-at-install opt-in (D-001) ---
    # When luks_enabled=True, partition_disk wraps the root partition in
    # LUKS2 (argon2id, 1 GB memory, 4 iter) before mkfs.ext4. The
    # passphrase rides install_io to the backend; never persisted to
    # state.yaml or any disk artifact. Cleared by clear_sensitive_data
    # after install completes (success OR failure path).
    luks_enabled: bool = False
    luks_passphrase: str = ""
    luks_passphrase_confirm: str = ""

    # --- D-001 EXPERIMENTAL unlock methods (operator Option A 2026-05-18T22:52Z) ---
    # tpm2_enabled / fido2_enabled compose with luks_enabled. Backend
    # disks.py adds a TPM2-sealed key + FIDO2 hmac-secret HMAC as
    # additional LUKS keyslots; fde-init.sh tries those before falling
    # through to the passphrase prompt. Both labeled EXPERIMENTAL in
    # the UI.
    tpm2_enabled: bool = False
    fido2_enabled: bool = False

    # --- Dual-boot detection (Option C 2026-05-24) ---
    # When True, GRUB_DISABLE_OS_PROBER=false is written to the installed
    # system's /etc/default/grub. os-prober runs at install time and on
    # every subsequent kernel update, scanning adjacent partitions for
    # Windows / other Linux distros and adding boot menu entries.
    # When False, GRUB_DISABLE_OS_PROBER=true (security-hardened default —
    # os-prober mounts adjacent partitions during grub-mkconfig, which is
    # an attack surface on systems where those partitions are untrusted).
    # Default True: most users installing on machines with another OS want
    # dual-boot to keep working; single-boot users see no behavior change
    # (os-prober finds nothing, adds nothing).
    detect_other_oses: bool = True

    # --- Default boot target (D1 / work-plan 1.25, decided 2026-07-08) ---
    # Whether InterGenOS becomes the default UEFI boot target. Tri-state:
    #   None  — not asked / not applicable (single-OS, non-EFI, or the foreign-
    #           entry probe was inconclusive). to_install_io() omits the key, so
    #           the backend keeps efibootmgr --create's historical prepend.
    #   True  — user asked InterGenOS to be the default boot target.
    #   False — user declined; the backend keeps the machine's prior default and
    #           places InterGenOS after it in BootOrder.
    # The Confirm screen only surfaces the choice when the backend probe
    # (bootloader.has_other_os_boot_entries()) finds another installed OS's boot
    # entry — otherwise silently taking over the boot order is the D1 Prime-
    # Directive concern. Default None preserves single-OS/BIOS behavior verbatim.
    make_default_boot: Optional[bool] = None

    # --- Wi-Fi carry (2026-07-11) ---
    # Tri-state, same shape as make_default_boot:
    #   None  — never asked: no active Wi-Fi connection at Confirm, or the
    #           probe was inconclusive. to_install_io() omits the key; the
    #           backend carries nothing.
    #   True  — carry the live session's Wi-Fi profile(s) onto the installed
    #           system (the seed when the ask renders: the user connected
    #           THIS machine to that network on purpose, so first boot
    #           arriving already-connected is the expected outcome).
    #   False — user opted out; nothing is copied.
    # The Confirm screen surfaces the choice only when the live session has
    # an active Wi-Fi connection (netprobe.active_wifi_names()).
    carry_wifi: Optional[bool] = None

    # --- User screen ---
    hostname: str = "intergenos"
    username: str = ""
    user_password: str = ""
    user_password_confirm: str = ""
    root_password: str = ""
    root_password_confirm: str = ""
    mok_password: str = ""
    # Non-sensitive record of the MOK choice: True when the user opted into
    # MOK enrollment by setting a (validated) passphrase on the User screen.
    # Exists because clear_sensitive_data() zeroes mok_password from BOTH
    # Progress-page outcome paths BEFORE the Done page loads — the Done
    # page keys its Secure-Boot re-enable guidance on this flag, never on
    # the scrubbed credential.
    mok_enrollment_chosen: bool = False

    # --- D-2: Secure Boot-aware MOK-skip guard (HARD BLOCK) ---
    # secure_boot_enabled is probed at Confirm time via
    # installer.backend.secureboot.is_secure_boot_enabled(): True = SB
    # enforcing, False = SB off, None = unknown (non-EFI / unreadable).
    # When SB is enforcing and the user skips MOK enrollment, the install is
    # BLOCKED (mok_install_blocked()) — there is no acknowledge-and-proceed
    # escape, because proceeding would hand the user a guaranteed-unbootable
    # system. The two non-bricking paths: enroll a MOK (set a password) or
    # disable Secure Boot in firmware. (decided 2026-06-05, replacing
    # the earlier ack checkbox.)
    secure_boot_enabled: Optional[bool] = None
    # firmware_is_efi distinguishes secure_boot_enabled's two None cases
    # (probed at Confirm time via secureboot.is_efi_firmware()): None+EFI =
    # SB state unreadable on a UEFI host (might be enforcing -> soft note);
    # None+non-EFI = BIOS, MOK irrelevant -> benign. See
    # mok_skip_sb_unknown_on_efi() (D-2 hardening, reviewed).
    firmware_is_efi: bool = False

    # --- Package groups (collected here for now; later phase may move to
    # a dedicated screen if owner pulls package-toggles into the flow) ---
    package_groups: List[str] = field(
        default_factory=lambda: ["core", "base", "desktop-gnome"]
    )

    # --- D-010 InterGen AI opt-in (default NO by requirement) ---
    # When True, PHASE_SERVICES runs `systemctl --global enable
    # intergen.service` in the target chroot so the assistant starts
    # at first login. Default OFF: the service is installed but
    # remains disabled; the user can opt in later via
    # `systemctl --user enable intergen.service`. The Forge Packages
    # screen + TUI walking sequence both surface this prompt.
    intergen_ai_enable: bool = False

    # --- SSH server opt-in (D-019 amends D-007 sshd-default arm) ---
    # When True, PHASE_SERVICES enables sshd.service AND opens TCP/22
    # inbound in /etc/nftables.conf on the target. Default OFF: sshd
    # is installed but not enabled, firewall port 22 stays closed.
    # User can opt in later by running `systemctl enable --now sshd`
    # AND adding a port-22 accept rule to /etc/nftables.conf (or
    # `pkm remove intergenos-firewall-defaults` to take their firewall
    # in hand entirely). The Forge Packages screen + TUI walking
    # sequence both surface this prompt.
    ssh_server_enable: bool = False

    # SSH public key for the Forge-created user (optional; closes
    # audit-row sshd-password-auth via key-only posture when provided).
    # When ssh_server_enable=True AND this is non-empty:
    #   1. PHASE_SERVICES writes the key to /home/<username>/.ssh/
    #      authorized_keys (0600 perms; .ssh dir 0700; owned by the
    #      new user).
    #   2. openssh ships a 02-intergenos-keys-only.conf drop-in
    #      disabling PasswordAuthentication + ChallengeResponse +
    #      KbdInteractive (PubkeyAuthentication yes is upstream default
    #      but set explicit).
    # When ssh_server_enable=True AND this is empty: password
    # authentication stays on (upstream default); user can SSH in
    # with their account password.
    # When ssh_server_enable=False: this field is ignored.
    # Decided 2026-05-22 (Option C): SSH opt-in PLUS
    # optional key paste lets the user pick the security tradeoff
    # without locking themselves out.
    ssh_public_key: str = ""


    # --- Progress screen state ---
    install_started: bool = False
    install_completed: bool = False
    install_failed: bool = False
    install_cancelled: bool = False
    install_error_message: str = ""

    def __post_init__(self):
        # Invariant: 'core' is always in package_groups. The orchestrator's
        # validate phase rejects yaml that omits core (it's the LFS Ch 8
        # substrate — an installed system without it cannot boot). Enforce
        # the invariant at construction time so no code path — including
        # a future package-selection screen that toggles checkboxes — can
        # produce a state where core is absent.
        if "core" not in self.package_groups:
            self.package_groups = ["core"] + list(self.package_groups)

    def clear_sensitive_data(self) -> None:
        """Zero out password + MOK fields after install completes.

        Best-effort residual-credentials-in-memory mitigation: if a crash
        dump or core file is generated post-install, plaintext passwords
        should not be recoverable from this dataclass instance.

        Note: Python strings are immutable, so "zeroing" only drops THIS
        object's reference. The original string objects may still exist
        elsewhere in memory until garbage-collected (and even then, the
        underlying bytes may persist on the heap until reused). This is
        a defense-in-depth layer, not a cryptographic guarantee.

        Called by ProgressPage from BOTH success and failure paths so a
        failed install also clears the credentials it captured. Does NOT
        clear `username` or `hostname` — those aren't sensitive in the
        same class (and may be needed for the Done page summary) — nor
        `mok_enrollment_chosen`, a boolean choice record (not a credential)
        the Done page keys its Secure-Boot guidance on after this scrub.
        """
        self.user_password = ""
        self.user_password_confirm = ""
        self.root_password = ""
        self.root_password_confirm = ""
        self.mok_password = ""
        # D-001: LUKS passphrase + confirm cleared too. The backend has
        # already piped the passphrase to cryptsetup over stdin and
        # zeroized its local copy by the time we reach here.
        self.luks_passphrase = ""
        self.luks_passphrase_confirm = ""

    def is_ready_for_install(self) -> bool:
        """All required fields populated + destructive op confirmed.

        The Confirm screen calls this before transitioning to Progress.
        """
        # D-001: when LUKS is opted-in, passphrase must be non-empty +
        # match its confirm. Otherwise the LUKS fields are irrelevant.
        luks_ok = (
            (not self.luks_enabled)
            or (
                bool(self.luks_passphrase)
                and self.luks_passphrase == self.luks_passphrase_confirm
            )
        )
        return (
            self.target_disk is not None
            and self.confirm_destructive
            and bool(self.username)
            and bool(self.user_password)
            and self.user_password == self.user_password_confirm
            and bool(self.root_password)
            and self.root_password == self.root_password_confirm
            and luks_ok
            # D-2 (decided 2026-06-05, HARD BLOCK): skipping MOK
            # enrollment while Secure Boot is enforcing produces a guaranteed-
            # unbootable install, so the installer must NOT proceed. The only
            # ways forward are to enroll a MOK (set a MOK password) or disable
            # Secure Boot in firmware — there is no "acknowledge and install
            # anyway" escape. MOK stays OPTIONAL when SB is OFF.
            and not self.mok_install_blocked()
        )

    def mok_install_blocked(self) -> bool:
        """True when the install MUST be blocked: the user skipped MOK
        enrollment WHILE Secure Boot is KNOWN-enforcing — the silent-brick
        footgun (D-2). The boot chain would be signed with a MOK that was
        never enrolled -> unbootable under Secure Boot, and the "re-enroll
        later with mokutil" recovery is unreachable because the system won't
        boot. So the Confirm screen surfaces the consequence and the install
        cannot proceed until the user EITHER enrolls a MOK (sets a password)
        OR disables Secure Boot in firmware (decided hard block,
        2026-06-05 — replaces the earlier acknowledge-and-proceed gate).

        Returns False when SB is off (False), unknown / non-EFI / unreadable
        (None), or a MOK password was set (enrollment chosen). The `is True`
        test is deliberate: an unknown SB state does NOT raise a scary
        (possibly-false) block, which would wrongly stop the SB-off majority;
        that case gets the softer informational note (mok_skip_sb_unknown_on_efi).
        MOK enrollment is OPTIONAL when Secure Boot is OFF — we only force the
        choice when not choosing guarantees an unbootable system.
        """
        return self.secure_boot_enabled is True and not self.mok_password

    # Back-compat alias: older call sites / tests referenced mok_skip_needs_ack.
    # The semantics are identical (SB-on + no MOK); only the install-gate
    # behavior changed (hard block, no ack escape).
    mok_skip_needs_ack = mok_install_blocked

    def mok_skip_sb_unknown_on_efi(self) -> bool:
        """True when MOK is skipped on a UEFI host whose Secure Boot state
        could NOT be determined (D-2 hardening, reviewed 2026-05-29).

        is_secure_boot_enabled() returns None for BOTH non-EFI/BIOS and
        EFI-but-unreadable (non-root caller / broken efivarfs). On a UEFI host
        where the read failed, SB *might* be enforcing — a silent benign skip
        would narrowly reintroduce the exact brick D-2 targets. This surfaces a
        SOFTER informational note (not the hard ack-gate, which stays reserved
        for KNOWN-enforcing), so the BIOS majority sees nothing. Rare in
        practice: the Forge installer runs as root, so the var normally reads.
        """
        return (
            self.secure_boot_enabled is None
            and self.firmware_is_efi
            and not self.mok_password
        )

    def mok_skip_efi_sb_off(self) -> bool:
        """True when MOK is skipped on a UEFI host whose Secure Boot is KNOWN-OFF
        (D2 / work-plan 1.22, PI-Z18).

        The pre-D2 Confirm screen lumped this case with BIOS ("skip is benign").
        It isn't: on a UEFI machine the user can later enable Secure Boot in
        firmware, and then the un-staged MOK means the signed boot chain won't
        validate — recoverable only by a manual `mokutil --import`. This surfaces
        a LOUD consequence note but does NOT gate the install — skipping stays a
        valid choice (never forced), unlike mok_install_blocked() which hard-
        blocks. Distinct from the genuine BIOS case (firmware_is_efi is False),
        where Secure Boot can never apply and the skip is truly benign.
        """
        return (
            self.secure_boot_enabled is False
            and self.firmware_is_efi
            and not self.mok_password
        )

    # ----------------------------------------------------------------------
    # Phase 4 orchestrator interface — yaml builder + install_io contract
    # ----------------------------------------------------------------------

    def build_install_yaml(self) -> Dict[str, Any]:
        """Emit the yaml-schema-v1 dict consumed by `run_install`.

        Schema v1 (per `installer/data/install-schema.yaml`):
          required: locale, timezone, hostname, package_groups
          optional: keymap (orchestrator falls back to "us" if absent)

        `core` is force-included here via set-union as defense-in-depth
        against post-construction mutation of `package_groups` that
        bypasses `__post_init__`. The orchestrator's validate phase also
        rejects yaml that omits it; this layer enforces the same invariant
        at the emission boundary so the yaml is correct even when a
        future package-selection screen (or test fixture) reassigns
        `package_groups` after construction.

        Disk + passwords + username are deliberately NOT in this dict —
        they are install_io collected interactively (Q-TUI-INTERACTIVITY=B
        + Q-GUI-SCREENS=7). Pre-seeding disk = fat-finger risk; pre-seeding
        password = supply-chain risk. PRIME DIRECTIVE.
        """
        chosen = sorted(set(self.package_groups) | {"core"})
        return {
            "version": YAML_SCHEMA_VERSION,
            "locale": self.locale,
            "timezone": self.timezone,
            "hostname": self.hostname,
            "package_groups": chosen,
            "keymap": self.keymap,
        }

    def write_install_yaml(self, path=DEFAULT_YAML_PATH) -> Path:
        """Serialize `build_install_yaml()` to disk via PyYAML.

        PyYAML is already an installer dep (used by the orchestrator's
        `load_yaml_config`); using `yaml.safe_dump` here keeps the round-
        trip deterministic without re-implementing emission like the TUI
        does. The TUI's hand-rolled writer exists to keep the install-time
        surface dep-free; the GUI's heavyweight Gtk import graph already
        rules that constraint out.
        """
        import yaml as _yaml

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        cfg = self.build_install_yaml()
        with p.open("w", encoding="utf-8") as f:
            f.write("# Forge install config — generated at install time by the\n")
            f.write("# Forge GUI installer. Ephemeral (lives on the live overlay;\n")
            f.write("# not persisted to the installed target).\n")
            _yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)
        return p

    def to_install_io(self) -> Dict[str, Any]:
        """Emit the install_io dict consumed by `run_install`.

        Required keys per `REQUIRED_INSTALL_IO_FIELDS`:
          disk, username, user_password, root_password
        Optional keys honoured by the orchestrator:
          mok_password (triggers MOK enrollment queue on EFI installs)
          user_groups (default groups applied if absent)

        Empty / None values are preserved verbatim so that the
        orchestrator's validation can surface the missing-field error
        with the same message the TUI would produce.
        """
        io: Dict[str, Any] = {
            "disk": self.target_disk,
            "username": self.username,
            "user_password": self.user_password,
            "root_password": self.root_password,
            # Option C 2026-05-24: always thread through (default differs
            # from backend's absent-default semantics — backend's .get()
            # default is also True, but emit explicitly so the install.yaml
            # is unambiguous about which lane the user picked).
            "detect_other_oses": self.detect_other_oses,
        }
        # D1 / work-plan 1.25: thread the default-boot-target choice ONLY when
        # it was actually asked (make_default_boot is not None). An absent key
        # makes the backend keep efibootmgr --create's prepend — correct for
        # single-OS / non-EFI installs where there is no prior default to
        # respect. See make_default_boot's field doc for the tri-state.
        if self.make_default_boot is not None:
            io["make_default_boot"] = self.make_default_boot
        # Wi-Fi carry (2026-07-11): same only-when-asked threading — an
        # absent key tells the backend the ask never rendered (nothing to
        # carry); see carry_wifi's field doc for the tri-state.
        if self.carry_wifi is not None:
            io["carry_wifi"] = self.carry_wifi
        if self.mok_password:
            io["mok_password"] = self.mok_password
        # D-010 InterGen AI opt-in: thread through only when the user
        # opted in; absent key is equivalent to intergen_ai_enable=False
        # per the backend's install_io.get("intergen_ai_enable") read
        # pattern in PHASE_SERVICES.
        if self.intergen_ai_enable:
            io["intergen_ai_enable"] = True
        # D-019 SSH server opt-in: same absent-equals-False pattern.
        # Backend's PHASE_SERVICES reads install_io.get("ssh_server_enable").
        if self.ssh_server_enable:
            io["ssh_server_enable"] = True
            # SSH public key (optional; only meaningful when ssh_server_
            # enable is True). When present, PHASE_SERVICES writes
            # authorized_keys + ships the keys-only sshd_config drop-in.
            if self.ssh_public_key.strip():
                io["ssh_public_key"] = self.ssh_public_key.strip()
        # D-001 LUKS opt-in: only thread through when the user opted in;
        # absent keys are equivalent to luks_enabled=False per the
        # backend's install_io.get("luks_enabled") read pattern.
        if self.luks_enabled:
            io["luks_enabled"] = True
            io["luks_passphrase"] = self.luks_passphrase
            # D-001 EXPERIMENTAL unlock methods compose with LUKS only
            # (backend validates this); thread through when enrolled.
            if self.tpm2_enabled:
                io["tpm2_enabled"] = True
            if self.fido2_enabled:
                io["fido2_enabled"] = True
        return io

    def to_run_install_kwargs(
        self,
        yaml_path,
        archive_dir,
        packages_dir=None,
        progress_callback: Optional[Callable] = None,
        dry_run: bool = False,
        target: Optional[str] = None,
        cancel_event=None,
    ) -> Dict[str, Any]:
        """Glue: bundle everything `run_install` needs into a single kwargs dict.

        Caller pattern (ProgressPage.on_load):
            yaml_path = state.write_install_yaml()
            kwargs = state.to_run_install_kwargs(
                yaml_path,
                self._window.archive_dir,
                packages_dir=self._window.packages_dir,
                progress_callback=self._on_progress_event,
                dry_run=self._window.dry_run,
                cancel_event=self._cancel_event,
            )
            result = run_install(**kwargs)
        """
        kwargs: Dict[str, Any] = {
            "yaml_path": str(yaml_path),
            "install_io": self.to_install_io(),
            "archive_dir": str(archive_dir) if archive_dir else None,
            "packages_dir": str(packages_dir) if packages_dir else None,
            "progress_callback": progress_callback,
            "dry_run": dry_run,
        }
        if target is not None:
            kwargs["target"] = target
        if cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        return kwargs

    def validation_errors(self) -> List[str]:
        """Return a list of human-readable problems with the current state.

        Empty list = ready to install. Distinct from `is_ready_for_install`
        which only returns a bool — this one is for surfacing specific
        failures in the UI (or in tests). Mirrors the orchestrator's
        aggregate-then-raise validation philosophy.
        """
        errors: List[str] = []
        if not self.target_disk:
            errors.append("target disk not set")
        if not self.confirm_destructive:
            errors.append("destructive operation not confirmed")
        if not self.username:
            errors.append("username not set")
        if not self.user_password:
            errors.append("user password not set")
        if self.user_password != self.user_password_confirm:
            errors.append("user passwords don't match")
        if not self.root_password:
            errors.append("root password not set")
        if self.root_password != self.root_password_confirm:
            errors.append("root passwords don't match")
        if not self.hostname:
            errors.append("hostname not set")
        # D-001 LUKS validation (mirrors is_ready_for_install)
        if self.luks_enabled:
            if not self.luks_passphrase:
                errors.append("LUKS passphrase not set (encryption opt-in active)")
            elif self.luks_passphrase != self.luks_passphrase_confirm:
                errors.append("LUKS passphrases don't match")
        # 'core' invariant: enforced at construction in __post_init__ AND
        # re-checked here as defense-in-depth. Post-construction mutation
        # of `package_groups` (test fixtures, future package-selection
        # screen toggling) can circumvent __post_init__, so the UI
        # validation surface must catch the omission too. Composes with
        # the set-union force-include in build_install_yaml.
        if "core" not in self.package_groups:
            errors.append("core package group is required (cannot be removed)")
        # D-2 (HARD BLOCK): MOK skipped under enforcing Secure Boot would brick
        # the install, so it cannot proceed — enroll a MOK or disable SB.
        if self.mok_install_blocked():
            errors.append(
                "Secure Boot is ON and MOK enrollment is skipped — set a MOK "
                "password to enroll it, or disable Secure Boot in firmware. "
                "The installer cannot continue: this install would not boot."
            )
        return errors

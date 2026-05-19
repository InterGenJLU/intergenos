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
    """Mutable state shared across all 7 screens.

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

    # --- User screen ---
    hostname: str = "intergenos"
    username: str = ""
    user_password: str = ""
    user_password_confirm: str = ""
    root_password: str = ""
    root_password_confirm: str = ""
    mok_password: str = ""

    # --- Package groups (collected here for now; later phase may move to
    # a dedicated screen if owner pulls package-toggles into the flow) ---
    package_groups: List[str] = field(
        default_factory=lambda: ["core", "base", "desktop-gnome"]
    )

    # --- D-010 InterGen AI opt-in (default NO per owner directive) ---
    # When True, PHASE_SERVICES runs `systemctl --global enable
    # intergen.service` in the target chroot so the assistant starts
    # at first login. Default OFF: the service is installed but
    # remains disabled; the user can opt in later via
    # `systemctl --user enable intergen.service`. The Forge Packages
    # screen + TUI walking sequence both surface this prompt.
    intergen_ai_enable: bool = False

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
        same class (and may be needed for the Done page summary).
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
        }
        if self.mok_password:
            io["mok_password"] = self.mok_password
        # D-010 InterGen AI opt-in: thread through only when the user
        # opted in; absent key is equivalent to intergen_ai_enable=False
        # per the backend's install_io.get("intergen_ai_enable") read
        # pattern in PHASE_SERVICES.
        if self.intergen_ai_enable:
            io["intergen_ai_enable"] = True
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
        return errors

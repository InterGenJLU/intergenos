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
    target_disk: Optional[str] = None
    install_mode: str = "fresh"  # one of: fresh, alongside
    alongside_partition: Optional[str] = None  # only used when install_mode=="alongside"
    confirm_destructive: bool = False

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

    # --- Progress screen state ---
    install_started: bool = False
    install_completed: bool = False
    install_failed: bool = False
    install_error_message: str = ""

    def is_ready_for_install(self) -> bool:
        """All required fields populated + destructive op confirmed.

        The Confirm screen calls this before transitioning to Progress.
        """
        return (
            self.target_disk is not None
            and self.confirm_destructive
            and bool(self.username)
            and bool(self.user_password)
            and self.user_password == self.user_password_confirm
            and bool(self.root_password)
            and self.root_password == self.root_password_confirm
        )

    # ----------------------------------------------------------------------
    # Phase 4 orchestrator interface — yaml builder + install_io contract
    # ----------------------------------------------------------------------

    def build_install_yaml(self) -> Dict[str, Any]:
        """Emit the yaml-schema-v1 dict consumed by `run_install`.

        Schema v1 (per `installer/data/install-schema.yaml`):
          required: locale, timezone, hostname, package_groups
          optional: keymap (orchestrator falls back to "us" if absent)

        `core` is force-included even if the user un-toggled in package
        selection — Schema says core is required and the orchestrator's
        validate phase rejects yaml that omits it. We force it here so a
        stale checkbox state can never produce an unbootable install.

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
          install_mode (currently informational; alongside-partition flow
            not yet wired through orchestrator)
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
        if self.install_mode and self.install_mode != "fresh":
            io["install_mode"] = self.install_mode
        return io

    def to_run_install_kwargs(
        self,
        yaml_path,
        archive_dir,
        packages_dir=None,
        progress_callback: Optional[Callable] = None,
        dry_run: bool = False,
        target: Optional[str] = None,
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
        if "core" not in (self.package_groups or []):
            # Surfaced as a soft warning at the UI layer — build_install_yaml
            # force-includes core anyway, but if a test or a future code path
            # bypasses build_install_yaml it'd be useful to flag.
            errors.append("package group 'core' is required")
        return errors

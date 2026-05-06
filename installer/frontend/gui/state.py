"""Central installer state passed between screens.

One dataclass instance flows through the NavigationView. Each screen reads
the fields it cares about on entry and writes back on next/confirm.

Why a dataclass and not (say) a Gtk.Stack-shared dict: dataclasses give us
type hints + IDE autocomplete + a single audit point for what an install
actually requires. Adding a field is one line in this file; forgetting to
populate it surfaces as an attribute error rather than a silent KeyError.
"""

from dataclasses import dataclass, field
from typing import List, Optional


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

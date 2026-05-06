"""Forge — InterGenOS System Installer — GTK4/libadwaita GUI.

Scaffolding for the 7-screen flow (per Q-GUI-SCREENS resolved 2026-05-06):

    Welcome → Keyboard/Locale/Timezone → Disk → User → Confirm → Progress → Done

"Disk before User" matches the modal pattern across Calamares / Pop!_OS / Ubiquity:
locale-y stuff up front, destructive disk decision before the user account, then
final confirm + progress + done.

Phase 4 kickoff scope is *scaffolding* — placeholder screen content, real
navigation, real state-passing via the central InstallerState dataclass at
`gui.state`. Actual screen content fills in later phases.
"""

from .window import run_installer  # noqa: F401  (re-export for __main__ dispatch)

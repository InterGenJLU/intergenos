"""Forge GUI — 7-screen package.

Screens are ordered as the user encounters them. The window's
NavigationView pre-instantiates all 7 from `SCREEN_ORDER` so back/forward
navigation returns to the same widget instance the user previously saw
(state in entry widgets persists without round-tripping through
InstallerState).

Order (per Q-GUI-SCREENS=7 resolved 2026-05-06):
    Welcome → Keyboard/Locale/TZ → Disk → User → Confirm → Progress → Done

"Disk before User" matches the modal pattern across Calamares / Pop!_OS /
Ubiquity: locale-y stuff up front, destructive disk decision before the
user account, then final confirm + progress + done.
"""

from .confirm import ConfirmPage
from .disk import DiskPage
from .done import DonePage
from .keyboard_locale import KeyboardLocalePage
from .progress import ProgressPage
from .user import UserPage
from .welcome import WelcomePage


SCREEN_ORDER = [
    WelcomePage,
    KeyboardLocalePage,
    DiskPage,
    UserPage,
    ConfirmPage,
    ProgressPage,
    DonePage,
]


__all__ = [
    "SCREEN_ORDER",
    "WelcomePage",
    "KeyboardLocalePage",
    "DiskPage",
    "UserPage",
    "ConfirmPage",
    "ProgressPage",
    "DonePage",
]

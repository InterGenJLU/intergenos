"""Forge GUI — 8-screen package.

Screens are ordered as the user encounters them. The window's
NavigationView pre-instantiates all 8 from `SCREEN_ORDER` so back/forward
navigation returns to the same widget instance the user previously saw
(state in entry widgets persists without round-tripping through
InstallerState).

Order (originally Q-GUI-SCREENS=7; PackagesPage added 2026-05-16 per
owner ratification "all expected features present"):
    Welcome → Keyboard/Locale/TZ → Disk → User → Packages → Confirm
    → Progress → Done

"Disk before User" matches the modal pattern across Calamares / Pop!_OS /
Ubiquity: locale-y stuff up front, destructive disk decision before the
user account, then final confirm + progress + done.

"Packages after User" matches Calamares / Anaconda: the user is already
oriented to which machine + which account they're configuring; package
selection then feels like "what software gets installed for THIS user
on THIS machine," not a context-free checklist.
"""

from .confirm import ConfirmPage
from .disk import DiskPage
from .done import DonePage
from .keyboard_locale import KeyboardLocalePage
from .packages import PackagesPage
from .progress import ProgressPage
from .user import UserPage
from .welcome import WelcomePage


SCREEN_ORDER = [
    WelcomePage,
    KeyboardLocalePage,
    DiskPage,
    UserPage,
    PackagesPage,
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
    "PackagesPage",
    "ConfirmPage",
    "ProgressPage",
    "DonePage",
]

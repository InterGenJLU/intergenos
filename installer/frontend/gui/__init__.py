"""Forge — InterGenOS System Installer — GTK4/libadwaita GUI.

The 7-screen flow (per Q-GUI-SCREENS resolved 2026-05-06):

    Welcome → Keyboard/Locale/Timezone → Disk → User → Confirm → Progress → Done

"Disk before User" matches the modal pattern across Calamares / Pop!_OS / Ubiquity:
locale-y stuff up front, destructive disk decision before the user account, then
final confirm + progress + done.

This package is intentionally light at import time — `state` is pure
Python (no Gtk dep), and the heavyweight gi/Gtk imports live in
`window.py` and `screens/*.py`. Test rigs and headless tooling can import
`installer.frontend.gui.state` without pulling in PyGObject. The
application entry point `run_installer` is imported directly from
`window.py` by `installer/__main__.py` dispatch — see that module.
"""

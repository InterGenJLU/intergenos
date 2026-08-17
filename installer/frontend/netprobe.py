# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Live-session network probes shared by the GTK and TUI frontends.

Wi-Fi carry (2026-07-11): both frontends gate the "Carry Wi-Fi connection"
ask on the live session actually having an active Wi-Fi connection — the
r15/D1 ask-only-when-it-matters shape. The probe runs as the live user via
nmcli (NetworkManager answers over D-Bus, no root needed); the backend's
carry_wifi_connections() does the authoritative root-side keyfile work at
install time.
"""

from __future__ import annotations

import subprocess

_WIFI_TYPES = ("802-11-wireless", "wifi")


def active_wifi_names():
    """Names of the live session's ACTIVE Wi-Fi connections.

    Returns a list of connection names (possibly empty), or None when the
    state is UNDETERMINABLE (nmcli missing, NetworkManager not running,
    timeout). None hides the ask — never surface a choice we cannot ground
    (the same inconclusive-probe discipline as has_other_os_boot_entries).
    """
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show",
             "--active"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    names = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        # nmcli -t escapes ':' inside values as '\:'; TYPE is the last
        # field and never contains one, so split from the right.
        name, ctype = line.rsplit(":", 1)
        if ctype in _WIFI_TYPES:
            names.append(name.replace("\\:", ":"))
    return names

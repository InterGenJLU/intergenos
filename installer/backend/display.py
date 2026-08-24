# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# display.py — read the live install session's monitor state and synthesize
# the pre-configuration greeter layout (the "greeter seed").
#
# WHY: on a fresh install no user monitors.xml exists, so the target's first
# greeter renders mutter's clone-all fallback — stretched across every
# monitor at default scale on a multi-head box. The one honest source for a
# per-machine default is the live install session itself: Forge runs on the
# target hardware inside a GNOME session whose compositor has already
# negotiated every connector, and the PRIMARY logical monitor is literally
# the screen the installing user is looking at, at its real mode and scale.
# This module reads that state over the session bus (mutter's DisplayConfig
# GetCurrentState) and renders a monitors.xml enabling exactly the primary
# monitor, with every other connected monitor explicitly <disabled/> —
# mutter matches a stored configuration against the FULL connected set, so
# the disabled list is load-bearing, not decoration.
#
# Failure posture: every reader/synthesis error raises DisplayStateError;
# the caller (users.seed_greeter_monitor_layout) traces the reason and
# SKIPS — a missing seed degrades to the status-quo fallback greeter, never
# to a failed install. The seed must be best-effort by design: it depends
# on live-session runtime state (a session bus, a compositor), which a
# headless or TUI-over-serial install legitimately lacks.

import json
import subprocess
from xml.sax.saxutils import escape

# The live ISO's session user (the image default — framework-fixed).
LIVE_USER = "intergenos"
LIVE_RUNTIME_DIR = "/run/user/1000"

_BUSCTL_CALL = [
    "busctl", "--user", "--json=short", "call",
    "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
    "org.gnome.Mutter.DisplayConfig", "GetCurrentState",
]


class DisplayStateError(Exception):
    """The live session's display state could not be read or used."""


def read_live_display_state(user=LIVE_USER, runtime_dir=LIVE_RUNTIME_DIR,
                            _runner=subprocess.run, _pw_lookup=None):
    """GetCurrentState from the live session's mutter, as parsed JSON data.

    Runs busctl as the live session user against their session bus (the
    backend runs as root; setpriv drops to the bus owner so the connection
    authenticates as the session's own uid). setpriv is util-linux and on
    the image; runuser is NOT — it needs PAM and the live environment does
    not ship it, which skipped this seed on every install until measured
    on an installed ge9b-12 system. Returns the four-element
    GetCurrentState payload [serial, monitors, logical_monitors, props].
    """
    if _pw_lookup is None:
        import pwd
        _pw_lookup = pwd.getpwnam
    try:
        pw = _pw_lookup(user)
    except KeyError as e:
        raise DisplayStateError(f"live session user {user!r} not found: {e}")
    cmd = ["setpriv", f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}",
           "--init-groups", "env",
           f"XDG_RUNTIME_DIR={runtime_dir}",
           f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime_dir}/bus",
           *_BUSCTL_CALL]
    try:
        result = _runner(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DisplayStateError(f"busctl GetCurrentState failed to run: {e}")
    if result.returncode != 0:
        raise DisplayStateError(
            f"busctl GetCurrentState exited {result.returncode}: "
            f"{result.stderr.strip()[:300]}")
    try:
        payload = json.loads(result.stdout)["data"]
    except (ValueError, KeyError) as e:
        raise DisplayStateError(f"unparseable GetCurrentState output: {e}")
    if not isinstance(payload, list) or len(payload) != 4:
        raise DisplayStateError(
            f"unexpected GetCurrentState shape: {len(payload) if isinstance(payload, list) else type(payload)}")
    return payload


def _fmt_scale(scale):
    # mutter writes integral scales bare ("1") and fractional as-is ("1.5").
    return f"{int(scale)}" if float(scale) == int(scale) else f"{scale}"


def synthesize_primary_only_layout(state):
    """Render a monitors.xml enabling only the primary logical monitor.

    `state` is the GetCurrentState payload. The enabled monitor keeps its
    current mode (the is-current flag in the mode properties) and the
    primary logical monitor's scale; position is pinned to 0,0 (it is the
    only enabled monitor). Every OTHER connected monitor lands in
    <disabled/> so the configuration accounts for the full connected set.
    """
    _serial, monitors, logical_monitors, _props = state
    if not monitors:
        raise DisplayStateError("no connected monitors in GetCurrentState")

    primary = next((lm for lm in logical_monitors if lm[4]), None)
    if primary is None:
        # No primary flagged — a single-monitor state still has exactly one
        # logical monitor; take the first. Zero logical monitors is an error.
        if not logical_monitors:
            raise DisplayStateError("no logical monitors in GetCurrentState")
        primary = logical_monitors[0]
    scale = primary[2]
    primary_specs = primary[5]
    if not primary_specs:
        raise DisplayStateError("primary logical monitor lists no monitors")
    enabled_spec = primary_specs[0]  # (connector, vendor, product, serial)

    def _find_monitor(spec):
        for m in monitors:
            if list(m[0]) == list(spec):
                return m
        return None

    enabled = _find_monitor(enabled_spec)
    if enabled is None:
        raise DisplayStateError(
            f"primary spec {enabled_spec[0]} not in the monitor list")
    mode = next((md for md in enabled[1]
                 if md[6].get("is-current") in (True, {"type": "b", "data": True})),
                None)
    if mode is None:
        raise DisplayStateError(
            f"no current mode on primary monitor {enabled_spec[0]}")
    width, height, rate = mode[1], mode[2], mode[3]

    def _spec_xml(spec, indent):
        c, v, p, s = (escape(str(x)) for x in spec)
        i = " " * indent
        return (f"{i}<monitorspec>\n"
                f"{i}  <connector>{c}</connector>\n"
                f"{i}  <vendor>{v}</vendor>\n"
                f"{i}  <product>{p}</product>\n"
                f"{i}  <serial>{s}</serial>\n"
                f"{i}</monitorspec>\n")

    disabled = [m[0] for m in monitors if list(m[0]) != list(enabled_spec)]

    xml = ("<monitors version=\"2\">\n"
           "  <configuration>\n"
           "    <logicalmonitor>\n"
           "      <x>0</x>\n"
           "      <y>0</y>\n"
           f"      <scale>{_fmt_scale(scale)}</scale>\n"
           "      <primary>yes</primary>\n"
           "      <monitor>\n"
           + _spec_xml(enabled_spec, 8) +
           f"        <mode>\n"
           f"          <width>{int(width)}</width>\n"
           f"          <height>{int(height)}</height>\n"
           f"          <rate>{rate}</rate>\n"
           f"        </mode>\n"
           "      </monitor>\n"
           "    </logicalmonitor>\n")
    if disabled:
        xml += "    <disabled>\n"
        for spec in disabled:
            xml += _spec_xml(spec, 6)
        xml += "    </disabled>\n"
    xml += ("  </configuration>\n"
            "</monitors>\n")
    return xml


def synthesize_extended_layout(state):
    """Render a monitors.xml enabling EVERY connected monitor, side by side.

    WHY THIS EXISTS BESIDE THE SINGLE-PRIMARY ONE. Both layouts close the same
    race — a stored configuration means the compositor has nothing to settle
    while the first frame paints — but they answer different questions. The
    greeter is a login prompt on one screen, so single-primary is right there.
    The USER's session is the machine its owner plugged monitors into, and a
    seed that lists those monitors under <disabled/> switches them off at the
    moment the session starts: lit by the kernel through the whole boot scroll,
    dark from the first frame of the desktop. Measured on an installed
    three-head system on 2026-08-24.

    The primary keeps its live mode and scale and sits at 0,0; every other
    connected monitor follows it left to right at its own current mode and at
    the scale its live logical monitor uses. Positions are RECOMPUTED rather
    than copied from the live session: the installer session's arrangement may
    be mutter's clone-all fallback, in which case the live positions overlap
    and mutter would refuse the configuration. Laying them out end to end is
    valid whatever the live session was doing.

    A monitor with no current mode is skipped rather than guessed at, and if
    that leaves nothing enabled the caller gets a DisplayStateError — an empty
    configuration would be worse than no seed at all.
    """
    _serial, monitors, logical_monitors, _props = state
    if not monitors:
        raise DisplayStateError("no connected monitors in GetCurrentState")
    if not logical_monitors:
        raise DisplayStateError("no logical monitors in GetCurrentState")

    primary = next((lm for lm in logical_monitors if lm[4]), logical_monitors[0])
    primary_spec = list(primary[5][0]) if primary[5] else None
    if primary_spec is None:
        raise DisplayStateError("primary logical monitor lists no monitors")

    # The scale each monitor's own live logical monitor uses; the primary's
    # scale is the fallback for a monitor the live session had switched off.
    scale_of = {}
    for lm in logical_monitors:
        for spec in lm[5]:
            scale_of[tuple(spec)] = lm[2]
    default_scale = primary[2]

    ordered = [m for m in monitors if list(m[0]) == primary_spec]
    ordered += [m for m in monitors if list(m[0]) != primary_spec]

    entries = []
    for monitor in ordered:
        spec = list(monitor[0])
        mode = next((md for md in monitor[1]
                     if md[6].get("is-current") in (True, {"type": "b", "data": True})),
                    None)
        if mode is None:
            continue
        entries.append((spec, mode, scale_of.get(tuple(spec), default_scale)))
    if not entries:
        raise DisplayStateError(
            "no connected monitor reported a current mode")

    def _spec_xml(spec, indent):
        c, v, p, s = (escape(str(x)) for x in spec)
        i = " " * indent
        return (f"{i}<monitorspec>\n"
                f"{i}  <connector>{c}</connector>\n"
                f"{i}  <vendor>{v}</vendor>\n"
                f"{i}  <product>{p}</product>\n"
                f"{i}  <serial>{s}</serial>\n"
                f"{i}</monitorspec>\n")

    xml = "<monitors version=\"2\">\n  <configuration>\n"
    x = 0
    for index, (spec, mode, scale) in enumerate(entries):
        width, height, rate = mode[1], mode[2], mode[3]
        xml += ("    <logicalmonitor>\n"
                f"      <x>{x}</x>\n"
                "      <y>0</y>\n"
                f"      <scale>{_fmt_scale(scale)}</scale>\n")
        if index == 0:
            xml += "      <primary>yes</primary>\n"
        xml += ("      <monitor>\n"
                + _spec_xml(spec, 8) +
                "        <mode>\n"
                f"          <width>{int(width)}</width>\n"
                f"          <height>{int(height)}</height>\n"
                f"          <rate>{rate}</rate>\n"
                "        </mode>\n"
                "      </monitor>\n"
                "    </logicalmonitor>\n")
        x += round(int(width) / float(scale))
    xml += "  </configuration>\n</monitors>\n"
    return xml

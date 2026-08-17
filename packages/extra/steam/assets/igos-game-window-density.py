#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Match a Wine/Proton game prefix's interface density to the panel scale.

WHY THIS EXISTS
A Proton game prefix is created with the Wine default interface density
LogPixels=96 (96 dots per logical inch). On a display running at a scale
above 1.0 the compositor sizes the game's outer window frame correctly, but
everything Wine itself draws inside that frame -- the title bar, the
minimise/maximise/close buttons, Wine's own dialogs -- keeps rendering at
96 dpi and so appears at a fraction of its intended physical size. Setting
LogPixels to 96 x the panel scale in the prefix registry makes Wine draw at
the panel's density. Two registry locations must both be set, because Wine
reads the window-metric density from one and the font density from the other:

    HKCU\\Control Panel\\Desktop     LogPixels   (window metrics)
    HKCU\\Software\\Wine\\Fonts      LogPixels   (font rasterisation)

TWO WAYS TO APPLY IT, AND WHY BOTH SHIP
1. `sync-hook` writes a per-user protonfixes local default fix at
   ~/.config/protonfixes/localfixes/default.py. GE-Proton's protonfixes layer
   executes that file for EVERY game it launches, at both the `early` and
   `main` stages, and the density write then happens through Wine itself
   while Wine owns the prefix. That ordering matters: an edit made to
   user.reg from outside while a prefix is open is overwritten when Wine
   flushes its in-memory registry, and the hook route never races that way.
   The generated hook also invokes protonfixes' own shipped global default,
   because a local default file SUPPRESSES the shipped one -- see the
   comment in the generated text.
2. `apply` edits user.reg directly, for prefixes that are never launched
   through the hook (a different Proton build, a prefix used outside Steam).
   It refuses while Steam or any Wine process is running, precisely because
   of the flush-over-your-edit race described above.

READING THE PANEL SCALE, AND THE BOUNDS OF EACH SOURCE
GNOME's compositor is the only component that knows the scale actually in
effect, so it is asked first over D-Bus; that answer is correct under both
Wayland and X11 sessions but needs a session bus, so it is unavailable to a
root helper or a bare ssh shell. The saved GNOME monitor configuration file
is used as a last resort and is labelled as such, because it describes the
monitor set that was last configured rather than the one attached now.
Xft.dpi is deliberately NOT consulted under Wayland: GNOME publishes an
integer-rounded value to XWayland clients, measured as 192 on a 1.333-scale
panel, so it would silently produce a 2x density on a 1.333x display.
"""

import argparse
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

WINE_DEFAULT_DPI = 96

# A density below the Wine default would shrink the decorations this command
# exists to enlarge, and no shipping panel scale reaches 5x. A value outside
# the range means the scale was misread, so refuse rather than write it.
MIN_LOGPIXELS = 96
MAX_LOGPIXELS = 480

DESKTOP_SECTION = "Control Panel\\\\Desktop"
FONTS_SECTION = "Software\\\\Wine\\\\Fonts"
LOGPIXELS_VALUE_NAME = "LogPixels"

HOOK_RELPATH = Path(".config/protonfixes/localfixes/default.py")

# Every file this command generates carries this marker on its own line. A
# file at the hook path WITHOUT the marker was written by someone else and is
# never overwritten or deleted -- the user's own fix always wins.
HOOK_MARKER = "IGOS_GAME_WINDOW_DENSITY_GENERATED"

# Executable names whose presence means a prefix may be open. Matched against
# /proc/<pid>/comm, which the kernel derives from the executable, so this
# never matches a command line that merely mentions one of these names (a
# pattern match over argv reads the searcher's own process back as a hit).
BUSY_PROCESS_NAMES = frozenset(
    {
        "steam",
        "steamwebhelper",
        "wine",
        "wine64",
        "wine-preloader",
        "wine64-preloader",
        "wineserver",
        "proton",
    }
)


class DensityError(Exception):
    """A condition the caller must see; never swallowed into a default."""


# --------------------------------------------------------------------------
# panel scale
# --------------------------------------------------------------------------


def scale_from_environment_override():
    """Explicit scale from the environment, or None.

    Present so a user can pin a value their session cannot report and so the
    test suite can drive every downstream path without a display.
    """
    raw = os.environ.get("IGOS_GAME_WINDOW_DENSITY_SCALE")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise DensityError(
            "IGOS_GAME_WINDOW_DENSITY_SCALE is set to "
            f"{raw!r}, which is not a number."
        )
    if value <= 0:
        raise DensityError(
            "IGOS_GAME_WINDOW_DENSITY_SCALE is set to "
            f"{raw!r}, which is not above zero."
        )
    return value


def scale_from_compositor():
    """Scale of the primary logical monitor from GNOME's compositor, or None.

    Asks org.gnome.Mutter.DisplayConfig.GetCurrentState, whose third return
    value is the list of logical monitors; each entry carries its scale and a
    flag for whether it is the primary. This is the live value and is correct
    on both Wayland and X11 GNOME sessions. Returns None (never raises) when
    there is no session bus, no GNOME compositor, or no primary monitor --
    those are ordinary conditions on a headless or non-GNOME system, and the
    caller falls through to the next source.
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return None

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig",
            "GetCurrentState",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )
    except Exception:
        return None

    try:
        _serial, _monitors, logical_monitors, _properties = reply.unpack()
    except Exception:
        return None

    fallback = None
    for entry in logical_monitors:
        # (x, y, scale, transform, primary, monitors, properties)
        if len(entry) < 5:
            continue
        scale, primary = entry[2], entry[4]
        if fallback is None:
            fallback = scale
        if primary:
            return float(scale)
    return float(fallback) if fallback is not None else None


def scale_from_xft_dpi():
    """Scale derived from Xft.dpi, X11 sessions only, or None.

    Deliberately refuses to answer under Wayland. GNOME sets the Xft.dpi that
    XWayland clients see to an integer multiple of 96 regardless of a
    fractional panel scale -- measured as 192 on a panel whose logical scale
    is 1.333 -- so under Wayland this source reports a density half again as
    large as the truth.
    """
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "x11":
        return None
    xrdb = shutil.which("xrdb")
    if not xrdb:
        return None
    import subprocess

    try:
        out = subprocess.run(
            [xrdb, "-query"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() == "Xft.dpi":
            try:
                dpi = float(value.strip())
            except ValueError:
                return None
            if dpi > 0:
                return dpi / WINE_DEFAULT_DPI
    return None


def scale_from_saved_monitor_config(config_path=None):
    """Scale of the primary monitor in GNOME's SAVED configuration, or None.

    This file records the monitor set that was last configured, not the set
    attached now, so it is the last source consulted and the caller labels it
    as saved rather than live when it is what answered.
    """
    if config_path is None:
        config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        config_path = Path(config_home) / "monitors.xml"
    config_path = Path(config_path)
    if not config_path.is_file():
        return None
    try:
        root = ET.parse(config_path).getroot()
    except ET.ParseError:
        return None

    fallback = None
    for logical in root.iter("logicalmonitor"):
        scale_element = logical.find("scale")
        if scale_element is None or not (scale_element.text or "").strip():
            continue
        try:
            scale = float(scale_element.text.strip())
        except ValueError:
            continue
        if fallback is None:
            fallback = scale
        if logical.find("primary") is not None:
            return scale
    return fallback


def detect_scale():
    """Return (scale, source description) or raise DensityError.

    Refuses rather than assuming 1.0: a wrong density silently written into
    every future prefix is worse than a message naming --scale.
    """
    sources = (
        ("the IGOS_GAME_WINDOW_DENSITY_SCALE environment variable", scale_from_environment_override),
        ("the live GNOME compositor (org.gnome.Mutter.DisplayConfig)", scale_from_compositor),
        ("the X11 session's Xft.dpi resource", scale_from_xft_dpi),
        ("the saved GNOME monitor configuration (monitors.xml, which describes the last configured monitor set, not necessarily the attached one)", scale_from_saved_monitor_config),
    )
    for description, probe in sources:
        value = probe()
        if value is not None:
            return float(value), description
    raise DensityError(
        "Could not read the display scale from this session. Sources tried: "
        "the IGOS_GAME_WINDOW_DENSITY_SCALE environment variable, the GNOME "
        "compositor over D-Bus, the X11 Xft.dpi resource, and the saved GNOME "
        "monitor configuration. Pass --scale <number> to state it explicitly."
    )


def logpixels_for_scale(scale):
    """Wine LogPixels value for a display scale, or raise DensityError."""
    value = int(round(WINE_DEFAULT_DPI * float(scale)))
    if value < MIN_LOGPIXELS or value > MAX_LOGPIXELS:
        raise DensityError(
            f"A display scale of {scale} gives a Wine density of {value} dots "
            f"per inch, outside the accepted range {MIN_LOGPIXELS}-"
            f"{MAX_LOGPIXELS}. Refusing to write it."
        )
    return value


# --------------------------------------------------------------------------
# the protonfixes hook
# --------------------------------------------------------------------------


def hook_text(logpixels, scale, source):
    """The exact content of the generated protonfixes local default fix."""
    return f'''"""Set this game prefix's interface density to match the panel.

GENERATED FILE -- every line is rewritten by `igos-game-window-density
sync-hook`. Edits made here are lost the next time Steam starts. To take
this file over, delete the one-word marker assignment directly below this
text; nothing in InterGenOS rewrites or removes a file at this path once
that assignment is gone.

protonfixes runs this file for every game it launches, so the density write
happens through Wine while Wine owns the prefix -- an edit made to user.reg
from outside a running prefix is discarded when Wine next flushes its
registry.

Written for a display scale of {scale}, read from {source}.
"""

{HOOK_MARKER} = 1

# 96 dots per inch is the Wine default; this is 96 x the display scale.
IGOS_LOGPIXELS = {logpixels}


def _set_density() -> None:
    """Write LogPixels into both locations Wine reads it from."""
    from protonfixes import util
    from protonfixes.logger import log

    for key in ("HKCU\\\\Control Panel\\\\Desktop", "HKCU\\\\Software\\\\Wine\\\\Fonts"):
        util.regedit_add(key, "LogPixels", "REG_DWORD", str(IGOS_LOGPIXELS))
    log.info(f"igos: game window density set to {{IGOS_LOGPIXELS}} dots per inch")


def _run_shipped_default(game_id: str, stage: str) -> None:
    """Run the protonfixes default fix this file displaces.

    protonfixes runs a local default INSTEAD of its own shipped one, never in
    addition to it (protonfixes/fix.py run_fix). The shipped default is what
    turns the -pf_tricks, -pf_dxvk_set and -pf_replace_cmd Steam launch
    options into actions, so it is invoked here by the same private call
    run_fix would have made, and those launch options keep working.
    """
    from protonfixes import fix
    from protonfixes.config import config

    if config.main.enable_global_fixes:
        fix._run_fix(game_id, stage, True, False)


def _stage(game_id: str, stage: str) -> None:
    from protonfixes.logger import log

    if stage == "early":
        try:
            _set_density()
        except Exception as error:
            # A density failure must never stop a game from starting, but it
            # must never be silent either.
            log.warn(f"igos: could not set game window density: {{error}}")
    try:
        _run_shipped_default(game_id, stage)
    except Exception as error:
        log.warn(f"igos: could not run the shipped protonfixes default: {{error}}")


def early_with_id(game_id: str) -> None:
    _stage(game_id, "early")


def main_with_id(game_id: str) -> None:
    _stage(game_id, "main")
'''


def hook_path(home=None):
    home = Path(home) if home is not None else Path.home()
    return home / HOOK_RELPATH


def read_hook_ownership(path):
    """Return "absent", "ours", or "foreign" for a path we may write."""
    path = Path(path)
    if not path.exists():
        return "absent"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "foreign"
    return "ours" if HOOK_MARKER in text else "foreign"


def sync_hook(logpixels, scale, source, home=None, dry_run=False):
    """Write or refresh the generated hook. Returns a result dictionary.

    Never touches a file at the hook path that does not carry the marker, and
    never rewrites its own file when the content already matches -- so the
    per-launch call from the Steam wrapper is a no-op after the first run.
    """
    path = hook_path(home)
    ownership = read_hook_ownership(path)
    desired = hook_text(logpixels, scale, source)

    if ownership == "foreign":
        return {
            "path": str(path),
            "action": "left alone",
            "reason": (
                "a file written by someone else already provides the "
                "protonfixes default fix at this path"
            ),
        }
    if ownership == "ours" and path.read_text(encoding="utf-8") == desired:
        return {"path": str(path), "action": "already current", "reason": ""}
    if dry_run:
        return {
            "path": str(path),
            "action": "would write" if ownership == "absent" else "would refresh",
            "reason": "",
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    # protonfixes imports the containing directory as a package, so the
    # package marker has to exist for the import to resolve. protonfixes
    # creates it too; creating it here means the very first launch works.
    init_file = path.parent / "__init__.py"
    if not init_file.exists():
        init_file.write_text("", encoding="utf-8")
    action = "written" if ownership == "absent" else "refreshed"
    path.write_text(desired, encoding="utf-8")
    return {"path": str(path), "action": action, "reason": ""}


def remove_hook(home=None, dry_run=False):
    """Remove the generated hook, but only when it is the generated one."""
    path = hook_path(home)
    ownership = read_hook_ownership(path)
    if ownership == "absent":
        return {"path": str(path), "action": "not present", "reason": ""}
    if ownership == "foreign":
        return {
            "path": str(path),
            "action": "left alone",
            "reason": "this file was not generated by igos-game-window-density",
        }
    if dry_run:
        return {"path": str(path), "action": "would remove", "reason": ""}
    path.unlink()
    return {"path": str(path), "action": "removed", "reason": ""}


# --------------------------------------------------------------------------
# direct prefix edits
# --------------------------------------------------------------------------


def running_prefix_holders(proc_root="/proc"):
    """Names of running processes that may hold a Wine prefix open.

    Reads /proc/<pid>/comm, which the kernel fills from the executable name.
    Selecting on a command line instead would match this very process when it
    was started with one of these names as an argument.
    """
    found = set()
    root = Path(proc_root)
    if not root.is_dir():
        return found
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if comm in BUSY_PROCESS_NAMES:
            found.add(comm)
    return found


def steam_library_roots(home=None):
    """Every Steam library root that could hold compatdata prefixes."""
    home = Path(home) if home is not None else Path.home()
    roots = []
    candidates = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
    ]
    for base in candidates:
        vdf = base / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            try:
                text = vdf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for match in re.finditer(r'"path"\s+"([^"]+)"', text):
                roots.append(Path(match.group(1)))
        roots.append(base)

    seen = set()
    unique = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def find_prefixes(home=None, appid=None):
    """Paths of user.reg files for Steam compatibility prefixes."""
    results = []
    seen = set()
    for root in steam_library_roots(home):
        compatdata = root / "steamapps/compatdata"
        if not compatdata.is_dir():
            continue
        for entry in sorted(compatdata.iterdir()):
            if not entry.is_dir():
                continue
            if appid is not None and entry.name != str(appid):
                continue
            user_reg = entry / "pfx/user.reg"
            if not user_reg.is_file():
                continue
            try:
                resolved = user_reg.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            results.append((entry.name, user_reg))
    return results


def _dword(value):
    return f"dword:{value:08x}"


def read_logpixels(text):
    """Current LogPixels in each section of a user.reg text.

    Returns a dictionary keyed by section name; a section that is present but
    carries no LogPixels maps to None, and an absent section is not a key.
    """
    result = {}
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            end = stripped.find("]")
            if end > 0:
                section = stripped[1:end]
                result.setdefault(section, None)
            continue
        if section is None:
            continue
        match = re.match(r'"' + LOGPIXELS_VALUE_NAME + r'"\s*=\s*dword:([0-9a-fA-F]+)', stripped)
        if match:
            result[section] = int(match.group(1), 16)
    return result


def set_logpixels(text, logpixels):
    """Return user.reg text with LogPixels set in both sections.

    Rewrites an existing value in place, adds the value to a section that
    lacks it, and appends a whole section that is absent. The registry file's
    other content is left byte-identical.
    """
    wanted = _dword(logpixels)
    value_line = f'"{LOGPIXELS_VALUE_NAME}"={wanted}'
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"

    sections_needed = [DESKTOP_SECTION, FONTS_SECTION]
    handled = set()

    out = []
    current = None
    pending_section = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            end = stripped.find("]")
            if end > 0:
                # Leaving a target section that never carried the value: add
                # it before the next section header begins.
                if pending_section is not None:
                    out.append(value_line + newline)
                    handled.add(pending_section)
                    pending_section = None
                current = stripped[1:end]
                if current in sections_needed and current not in handled:
                    pending_section = current
            out.append(line)
            continue

        if (
            current in sections_needed
            and current not in handled
            and re.match(r'"' + LOGPIXELS_VALUE_NAME + r'"\s*=', stripped)
        ):
            indent = line[: len(line) - len(line.lstrip())]
            trailing = line[len(line.rstrip("\r\n")) :] or newline
            out.append(indent + value_line + trailing)
            handled.add(current)
            pending_section = None
            continue

        out.append(line)

    if pending_section is not None:
        out.append(value_line + newline)
        handled.add(pending_section)

    result = "".join(out)
    for section in sections_needed:
        if section in handled:
            continue
        if result and not result.endswith(("\n", "\r\n")):
            result += newline
        # Wine writes a modification time after each section header; a
        # section written without one is still read correctly, and Wine
        # supplies its own the next time it saves the file.
        result += f"{newline}[{section}] {int(time.time())}{newline}{value_line}{newline}"
    return result


def apply_to_prefix(user_reg, logpixels, dry_run=False, now=None):
    """Set the density in one prefix's user.reg. Returns a result dictionary.

    Makes a backup beside the file before the first change and makes no
    backup at all when the value is already correct, so repeated runs neither
    rewrite the registry nor litter the prefix with identical copies.
    """
    user_reg = Path(user_reg)
    text = user_reg.read_text(encoding="utf-8", errors="surrogateescape")
    current = read_logpixels(text)
    already = all(
        current.get(section) == logpixels for section in (DESKTOP_SECTION, FONTS_SECTION)
    )
    if already:
        return {
            "path": str(user_reg),
            "action": "already correct",
            "backup": None,
            "before": {k: current.get(k) for k in (DESKTOP_SECTION, FONTS_SECTION)},
        }

    updated = set_logpixels(text, logpixels)
    if dry_run:
        return {
            "path": str(user_reg),
            "action": "would update",
            "backup": None,
            "before": {k: current.get(k) for k in (DESKTOP_SECTION, FONTS_SECTION)},
        }

    stamp = now or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup = user_reg.with_name(user_reg.name + f".bak-pre-logpixels-{stamp}")
    shutil.copy2(user_reg, backup)
    user_reg.write_text(updated, encoding="utf-8", errors="surrogateescape")
    return {
        "path": str(user_reg),
        "action": "updated",
        "backup": str(backup),
        "before": {k: current.get(k) for k in (DESKTOP_SECTION, FONTS_SECTION)},
    }


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------


def resolve_density(args):
    """(logpixels, scale, source) from --scale or the session."""
    if args.scale is not None:
        scale, source = float(args.scale), "the --scale option"
    else:
        scale, source = detect_scale()
    return logpixels_for_scale(scale), scale, source


def cmd_show(args, out):
    logpixels, scale, source = resolve_density(args)
    print(f"display scale:  {scale}", file=out)
    print(f"read from:      {source}", file=out)
    print(f"Wine density:   {logpixels} dots per inch ({_dword(logpixels)})", file=out)

    path = hook_path(args.home)
    ownership = read_hook_ownership(path)
    described = {
        "absent": "not present",
        "ours": "present, generated by this command",
        "foreign": "present, written by someone else -- this command leaves it alone",
    }[ownership]
    print(f"per-game hook:  {path} -- {described}", file=out)

    prefixes = find_prefixes(args.home, appid=getattr(args, "appid", None))
    if not prefixes:
        print("game prefixes:  none found", file=out)
        return 0
    print("game prefixes:", file=out)
    for appid, user_reg in prefixes:
        try:
            values = read_logpixels(
                user_reg.read_text(encoding="utf-8", errors="surrogateescape")
            )
        except OSError as error:
            print(f"  {appid}: could not be read ({error})", file=out)
            continue
        desktop = values.get(DESKTOP_SECTION)
        fonts = values.get(FONTS_SECTION)
        unset = "unset, so Wine uses 96"
        state = (
            "matches the panel"
            if desktop == fonts == logpixels
            else "does not match the panel"
        )
        print(
            f"  {appid}: window metrics {unset if desktop is None else desktop}, "
            f"fonts {unset if fonts is None else fonts} -- {state}",
            file=out,
        )
    return 0


def cmd_sync_hook(args, out):
    logpixels, scale, source = resolve_density(args)
    result = sync_hook(logpixels, scale, source, home=args.home, dry_run=args.dry_run)
    message = f"{result['path']}: {result['action']}"
    if result["reason"]:
        message += f" ({result['reason']})"
    print(message, file=out)
    if not args.quiet and result["action"] in ("written", "refreshed", "would write", "would refresh"):
        print(
            f"Games launched through GE-Proton will use {logpixels} dots per "
            f"inch, matching a display scale of {scale}.",
            file=out,
        )
    return 0


def cmd_remove_hook(args, out):
    result = remove_hook(home=args.home, dry_run=args.dry_run)
    message = f"{result['path']}: {result['action']}"
    if result["reason"]:
        message += f" ({result['reason']})"
    print(message, file=out)
    return 0


def cmd_apply(args, out):
    logpixels, scale, source = resolve_density(args)
    busy = running_prefix_holders()
    if busy and not args.dry_run:
        raise DensityError(
            "Steam or a Wine process is running ("
            + ", ".join(sorted(busy))
            + "). A running prefix rewrites user.reg from memory and would "
            "discard this edit. Close Steam and any running game, then run "
            "this again."
        )

    prefixes = find_prefixes(args.home, appid=args.appid)
    if not prefixes:
        where = f"application id {args.appid}" if args.appid else "any Steam library"
        raise DensityError(f"No game prefix found for {where}.")

    print(
        f"Setting {logpixels} dots per inch (display scale {scale}, from {source}).",
        file=out,
    )
    for appid, user_reg in prefixes:
        result = apply_to_prefix(user_reg, logpixels, dry_run=args.dry_run)
        line = f"  {appid}: {result['action']}"
        if result["backup"]:
            line += f" (previous file kept at {result['backup']})"
        print(line, file=out)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="igos-game-window-density",
        description=(
            "Match a Wine or Proton game prefix's interface density to the "
            "display scale, so a windowed game's title bar and window buttons "
            "are drawn at the size the rest of the desktop uses."
        ),
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=None,
        help="display scale to use instead of the one read from the session",
    )
    parser.add_argument(
        "--home",
        default=None,
        help=argparse.SUPPRESS,  # test seam: redirect every path under one root
    )
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--quiet", action="store_true", help="print less")
    sub = parser.add_subparsers(dest="command")

    show = sub.add_parser("show", help="report the detected scale and every prefix's density")
    show.add_argument("--appid", default=None, help="report only this application id")
    show.set_defaults(func=cmd_show)

    sync = sub.add_parser(
        "sync-hook",
        help="write the per-game hook that sets the density when a game starts",
    )
    sync.set_defaults(func=cmd_sync_hook)

    unhook = sub.add_parser("remove-hook", help="remove the per-game hook")
    unhook.set_defaults(func=cmd_remove_hook)

    apply_cmd = sub.add_parser(
        "apply", help="edit a game prefix's registry directly (Steam must be closed)"
    )
    group = apply_cmd.add_mutually_exclusive_group(required=True)
    group.add_argument("--appid", default=None, help="apply to one application id")
    group.add_argument(
        "--all", action="store_true", help="apply to every game prefix found"
    )
    apply_cmd.set_defaults(func=cmd_apply)
    return parser


def main(argv=None, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help(out)
        return 2
    try:
        return args.func(args, out)
    except DensityError as error:
        print(f"igos-game-window-density: {error}", file=err)
        return 1


if __name__ == "__main__":
    sys.exit(main())

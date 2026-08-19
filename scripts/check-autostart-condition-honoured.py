#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed gate: no shipped autostart entry carries a dead condition.

THE CLASS. An `/etc/xdg/autostart/*.desktop` entry may carry
`AutostartCondition=`, a key that decides whether the entry runs — typically on
a GSettings value the user controls. GNOME's own session manager read it. GNOME
49 removed that machinery: upstream gnome-session's NEWS for 49.beta records
that "gnome-session's builtin service manager has been completely removed"
together with "various .desktop and .session file keys that were used only by
the builtin service manager", and gnome-session 49.2's source contains no
autostart-condition helper of any kind.

On a systemd-managed session the entries are converted instead by
systemd-xdg-autostart-generator, which delegates `AutostartCondition=` to a
separate binary, `gnome-systemd-autostart-condition`. With that binary absent
the generator logs

    ExecCondition executable gnome-systemd-autostart-condition not found,
    unit will not be started automatically

and then emits the unit anyway: the condition becomes a comment, the unit is
symlinked into xdg-desktop-autostart.target.wants like any other, and its only
remaining ExecCondition is the desktop-list one. Measured on systemd 259.1
against a constructed entry, that condition exits 0 — so the unit RUNS and the
author's condition is silently ignored. The generator's message describes the
opposite of what its output does, which is exactly why this needs a gate rather
than a reader's attention.

WHAT SAVES AN ENTRY, and it is unrelated to the condition: an entry carrying
`X-GNOME-Autostart-Phase=` is handled separately by the generator ("GNOME
startup phases are handled separately"), which either skips it outright or
marks the unit NotShowIn=GNOME. Measured three ways in one controlled run: no
phase key produced a runnable unit; Phase=Application and Phase=Initialization
each produced none.

THE RULE. An entry with `AutostartCondition=` and no `X-GNOME-Autostart-Phase=`
runs with its condition silently ignored. That is a switch the user believes
they control and do not, so it fails the build.

THE PREMISE IS RECHECKED, NOT ASSUMED. If `gnome-systemd-autostart-condition`
is present under the scanned root, the conditions ARE honoured and this class
does not exist; the gate reports that and passes rather than going on failing
entries for a reason that stopped being true.

Exit 0 clean, 1 on violations, 2 on usage/setup errors.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CONDITION_KEY = "AutostartCondition"
PHASE_KEY = "X-GNOME-Autostart-Phase"

# The generator resolves the helper on PATH; these are the locations a package
# would install it to on this system. Checked under the scanned root so the
# answer is about the tree being gated, not about the machine running the gate.
HELPER_NAME = "gnome-systemd-autostart-condition"
HELPER_DIRS = ("usr/libexec", "usr/lib/systemd", "usr/bin", "usr/local/bin")


def load_allowlist(path: Path) -> set[str]:
    """`<entry basename><tab or 2+ spaces><reason>`. An exception without a
    reviewable reason is not an exception."""
    names: set[str] = set()
    malformed: list[str] = []
    for i, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t+| {2,}", line, maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            malformed.append(f"line {i}: {raw!r}")
            continue
        names.add(parts[0].strip())
    if malformed:
        print("FATAL: malformed allowlist entries (reason column required):",
              file=sys.stderr)
        for m in malformed:
            print(f"  {m}", file=sys.stderr)
        raise SystemExit(2)
    return names


def read_keys(path: Path) -> dict[str, str]:
    """The [Desktop Entry] group's keys. Only the first group is read: a
    desktop file's action groups cannot carry either key we care about."""
    keys: dict[str, str] = {}
    in_group = False
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_group = line == "[Desktop Entry]"
            continue
        if not in_group or not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        keys.setdefault(k.strip(), v.strip())
    return keys


def find_helper(root: Path) -> Path | None:
    for d in HELPER_DIRS:
        p = root / d / HELPER_NAME
        if p.is_file():
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("/"),
                    help="filesystem root to scan (a chroot, or / for the "
                         "running system)")
    ap.add_argument("--allowlist", type=Path, required=True)
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"FATAL: root not found: {args.root}", file=sys.stderr)
        return 2
    autostart_dir = args.root / "etc/xdg/autostart"
    if not autostart_dir.is_dir():
        print(f"FATAL: no autostart directory at {autostart_dir} — this root "
              f"is not the one to gate", file=sys.stderr)
        return 2
    if not args.allowlist.is_file():
        print(f"FATAL: allowlist not found: {args.allowlist}", file=sys.stderr)
        return 2

    allowed = load_allowlist(args.allowlist)
    entries = sorted(p for p in autostart_dir.glob("*.desktop") if p.is_file())

    if not entries:
        # Never green on zero: an empty result cannot be told from a wrong
        # path, and this gate exists precisely to catch a silent nothing.
        print(f"FAIL — no autostart entries found under {autostart_dir}. A "
              f"gate that validated nothing cannot report a pass.")
        return 1

    helper = find_helper(args.root)
    if helper is not None:
        print(f"[autostart-condition] {HELPER_NAME} IS present under this root "
              f"({helper.relative_to(args.root)}), so AutostartCondition keys "
              f"are honoured and this class does not exist here.")
        print(f"[autostart-condition] PASS — {len(entries)} entry(ies) "
              f"scanned; the premise this gate rests on no longer holds and "
              f"the gate should be re-read before it is trusted again.")
        return 0

    violations: list[tuple[str, str]] = []
    phase_guarded: list[tuple[str, str]] = []
    allowlisted: list[str] = []
    with_condition = 0

    for entry in entries:
        keys = read_keys(entry)
        cond = keys.get(CONDITION_KEY)
        if not cond:
            continue
        with_condition += 1
        name = entry.name
        if name in allowed:
            allowlisted.append(name)
            continue
        if PHASE_KEY in keys:
            phase_guarded.append((name, keys[PHASE_KEY]))
            continue
        violations.append((name, cond))

    print(f"[autostart-condition] root {args.root}; {len(entries)} entry(ies) "
          f"scanned; {with_condition} carry {CONDITION_KEY}; "
          f"{HELPER_NAME} absent")

    if phase_guarded:
        # Said out loud on purpose: these are safe by an unrelated key, not by
        # anything anyone decided. If upstream drops the phase key the entry
        # becomes a violation with no other change.
        print(f"[autostart-condition] {len(phase_guarded)} entry(ies) carry a "
              f"dead condition but do NOT run under GNOME, because "
              f"{PHASE_KEY} makes the generator handle them separately:")
        for name, phase in phase_guarded:
            print(f"    {name}   ({PHASE_KEY}={phase})")

    if allowlisted:
        print(f"[autostart-condition] {len(allowlisted)} allowlisted with a "
              f"reason: {', '.join(sorted(allowlisted))}")

    if not violations:
        print(f"[autostart-condition] PASS — no entry would run with a "
              f"silently ignored condition.")
        return 0

    print(f"[autostart-condition] FAIL — {len(violations)} entry(ies) carry "
          f"{CONDITION_KEY} with no {PHASE_KEY}, so each one RUNS and its "
          f"condition is silently ignored:")
    for name, cond in violations:
        print(f"    {name}")
        print(f"        {CONDITION_KEY}={cond}")
    print("[autostart-condition] Disposition: drop the dead key from the "
          "shipped entry, give the entry a real gate the running session "
          "honours, or add a REASONED allowlist entry.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

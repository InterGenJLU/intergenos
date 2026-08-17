#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""check-setuid-inventory.py — fail-closed setuid/setgid + ownership gate.

Born from the GE-01 setuid-strip regression (L29, 2026-07-05): the staging
chokepoint's blanket `chown -R root:root` cleared suid/sgid on every
privileged binary in the corpus — the kernel clears those bits on any chown
of a regular file, even by root — and nothing between the archive and the
installed system refused. sudo/su/passwd/pkexec all shipped inert, on the
live ISO and on installed targets. This gate makes that class impossible to
seal again, in BOTH directions:

  1. STRIPPED-BIT / FLATTENED-OWNERSHIP arm — every inventory entry present
     on the chroot must carry exactly the declared mode, owner, and group.
  2. UNEXPECTED-PRIVILEGE arm — any suid/sgid regular file on the chroot
     NOT matched by the inventory refuses the seal (a setuid injection or
     an undeclared upstream addition must be triaged, never ridden).

An inventory path absent from the chroot is skipped — package presence is
owned by verify_paths (4.5) and the eviction dep rules, not this gate.

Owner/group names resolve against the CHROOT's etc/passwd + etc/group.

Usage: check-setuid-inventory.py --chroot /mnt/igos \
           [--inventory <repo>/config/setuid-inventory.txt]
Exit 0 = PASS; exit 1 = violations (each named); exit 2 = usage/inventory
error; exit 3 = empty audit (zero suid/sgid files found AND zero inventory
entries present — a gate that cannot see must halt, not wave through).
"""

import argparse
import fnmatch
import os
import stat
import sys
from pathlib import Path

# Pseudo-fs / volatile trees never audited (mirror of needclosure.py's skips).
SKIP_PREFIXES = ("proc", "sys", "dev", "run", "tmp", "mnt", "sources", "build")


def parse_inventory(path: Path):
    entries = []
    for ln, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            print(f"[setuid-gate] inventory syntax error at line {ln}: {raw!r}")
            sys.exit(2)
        glob, mode_s, owner, group = parts
        try:
            mode = int(mode_s, 8)
        except ValueError:
            print(f"[setuid-gate] bad octal mode at line {ln}: {mode_s!r}")
            sys.exit(2)
        entries.append((glob, mode, owner, group))
    if not entries:
        print(f"[setuid-gate] inventory {path} declares nothing — refusing")
        sys.exit(2)
    return entries


def load_ids(chroot: Path):
    """uid->name and gid->name maps from the chroot's own passwd/group."""
    users, groups = {}, {}
    for fname, table in (("etc/passwd", users), ("etc/group", groups)):
        f = chroot / fname
        if not f.is_file():
            print(f"[setuid-gate] {f} missing — cannot resolve names, refusing")
            sys.exit(2)
        for line in f.read_text().splitlines():
            bits = line.split(":")
            if len(bits) >= 3 and bits[2].isdigit():
                table[int(bits[2])] = bits[0]
    return users, groups


def main(argv=None):
    ap = argparse.ArgumentParser(description="setuid/setgid inventory gate")
    ap.add_argument("--chroot", required=True)
    ap.add_argument("--inventory", default=None,
                    help="default: <script-repo>/config/setuid-inventory.txt")
    args = ap.parse_args(argv)

    chroot = Path(args.chroot)
    inv_path = Path(args.inventory) if args.inventory else \
        Path(__file__).resolve().parent.parent / "config" / "setuid-inventory.txt"
    entries = parse_inventory(inv_path)
    users, groups = load_ids(chroot)

    violations = []
    matched_entries = set()
    privileged_found = 0

    for dirpath, dirnames, filenames in os.walk(chroot):
        rel_dir = os.path.relpath(dirpath, chroot)
        if rel_dir != "." and rel_dir.split(os.sep, 1)[0] in SKIP_PREFIXES:
            dirnames[:] = []
            continue
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = "/" + os.path.relpath(full, chroot)
            mode = stat.S_IMODE(st.st_mode)
            owner = users.get(st.st_uid, str(st.st_uid))
            group = groups.get(st.st_gid, str(st.st_gid))
            hit = None
            for e in entries:
                if fnmatch.fnmatch(rel, e[0]):
                    hit = e
                    break
            if hit:
                matched_entries.add(hit[0])
                if (mode, owner, group) != (hit[1], hit[2], hit[3]):
                    violations.append(
                        f"{rel}: is {mode:o} {owner}:{group}, inventory "
                        f"declares {hit[1]:o} {hit[2]}:{hit[3]} "
                        f"(stripped bit / flattened ownership)")
                if mode & 0o6000:
                    privileged_found += 1
            elif mode & 0o6000:
                privileged_found += 1
                violations.append(
                    f"{rel}: UNEXPECTED suid/sgid ({mode:o} {owner}:{group}) "
                    f"— not in {inv_path.name}; triage before sealing")

    print(f"[setuid-gate] audited chroot: {len(matched_entries)} inventory "
          f"entries present, {privileged_found} privileged file(s) seen, "
          f"{len(violations)} violation(s)")
    if not matched_entries and privileged_found == 0:
        print("[setuid-gate] EMPTY AUDIT — no inventory entry present and no "
              "privileged file found; a gate that cannot see must halt")
        sys.exit(3)
    if violations:
        for v in violations:
            print(f"[setuid-gate]   {v}")
        print("[setuid-gate] FAIL — refusing the seal")
        sys.exit(1)
    print("[setuid-gate] PASS — every present inventory entry exact; no "
          "unexpected privileged files")
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed configuration-baseline gate (build-squashfs Step 2.45).

pkm records, for every /etc file a package installs, the sha256 of the stock
it was first installed with (config_files.original_checksum) beside the
sha256 of the bytes the owning package registered (files.checksum). In a
build root the two MUST agree: nobody edits a file there, so a baseline that
names other bytes than the registered stock is one the lineage's earlier
release left behind when a rebuild re-registered the package (add_files keeps
the first baseline on a re-registration on purpose — on an installed machine
only the upgrade path moves it, after proving the person did not edit the
file). pkm's remover reads exactly that baseline when a package is removed: a
file whose live bytes differ from it is preserved as the person's edit. A
stale baseline therefore turns the squashfs prune (Step 2.5) into a leak —
the pruned package's stock file stays on the image owned by nobody, and the
ownership gate (Step 4.85) refuses the image hours later.

Measured 2026-10-16, halt #2 of the R001.3 mint: nvidia r17 and rsyslog r2,
rebuilt over the lineage's rows, carried the older releases' baselines and
their /etc files were preserved by the prune; 13 more rows, on packages the
ISO keeps, carried the same class with no symptom yet.

Checks, for every config_files row:

  1. the row's own package registers the path: its checksum is the stock;
     a baseline that differs is STALE                             -> RED
  2. the row's package does not register the path but exactly one other
     installed package does: judged against that owner, and said  -> RED/INFO
  3. the owner registered no checksum (a row imported without a hash):
     judged against the live file's bytes when it is readable      -> RED/INFO
  4. no installed package registers the path and it IS on disk: an /etc
     file the baseline table knows and the files table does not   -> RED
  5. no owner and not on disk: the residue row of a removed package —
     listed and counted, never a verdict on the image             -> INFO

The remedy for a RED row is the record, never this gate: in the root, prove
the live bytes are the registered stock, then `pkm refresh-baseline <path>`
re-records the baseline from them. Since 2026-10-16 both builders set the
baseline to the build's own stock at every registration, so this gate's job
is to catch the record a substrate carried in from before that.

Exit 0 clean, 1 on any RED, 2 on usage/setup errors. Read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROG = "config-baselines"

STALE = "STALE baseline (not the registered stock)"
UNREGISTERED_ON_DISK = ("baseline for an /etc file on disk that no installed "
                        "package registers")
OTHER_OWNER = ("judged against another package's registration (the row "
               "names a package that does not register the path)")
OWNERS_DISAGREE = "co-owners register different stock; unjudged"
NO_CHECKSUM_LIVE = "owner registered no checksum; judged against the live bytes"
NO_CHECKSUM_UNREADABLE = ("owner registered no checksum and the file is not "
                          "readable; unjudged")
NULL_BASELINE = "baseline unknown (NULL): pkm protects the file; unjudged"
RESIDUE = "residue row of a removed package (no owner, not on disk)"


def _sha256(path: str) -> str | None:
    """sha256 of the regular file at `path` (a final symbolic link is followed,
    as pkm's own baseline hashing follows it); None when unreadable."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def judge(conn: sqlite3.Connection, root: Path):
    """Classify every config_files row. Returns (rows, red, info) where red
    and info map a class label to the list of rendered rows in it."""
    owners: dict[str, dict[int, str | None]] = {}
    for path, pid, checksum in conn.execute(
            "SELECT path, package_id, checksum FROM files WHERE is_dir = 0"):
        owners.setdefault(path, {})[pid] = checksum
    names = {pid: f"{name}-{version}-{release}" for pid, name, version, release
             in conn.execute("SELECT id, name, version, release FROM installed")}
    rows = conn.execute(
        "SELECT path, package_id, original_checksum FROM config_files "
        "ORDER BY path").fetchall()
    red: dict[str, list[str]] = defaultdict(list)
    info: dict[str, list[str]] = defaultdict(list)

    def label(pid):
        return names.get(pid, f"package_id={pid}")

    for path, pid, baseline in rows:
        registered = owners.get(path, {})
        if pid in registered:
            owner, expected = pid, registered[pid]
        elif len(registered) == 1:
            owner, expected = next(iter(registered.items()))
            info[OTHER_OWNER].append(
                f"{path} [row: {label(pid)}; owner: {label(owner)}]")
        elif registered:
            info[OWNERS_DISAGREE].append(path)
            continue
        else:
            abs_path = root / path
            if os.path.lexists(abs_path):
                red[UNREGISTERED_ON_DISK].append(f"{path} [row: {label(pid)}]")
            else:
                info[RESIDUE].append(f"{path} [row: {label(pid)}]")
            continue
        if expected is None:
            abs_path = root / path
            expected = _sha256(str(abs_path)) if os.path.isfile(abs_path) else None
            if expected is None:
                info[NO_CHECKSUM_UNREADABLE].append(path)
                continue
            info[NO_CHECKSUM_LIVE].append(path)
        if baseline is None:
            info[NULL_BASELINE].append(path)
            continue
        if baseline != expected:
            red[STALE].append(
                f"{path} [{label(owner)}: baseline {baseline[:12]}… "
                f"registered {expected[:12]}…]")
    return rows, red, info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--chroot", type=Path, default=Path("/mnt/igos"))
    ap.add_argument("--db", type=Path, default=None,
                    help="pkm database (default: <chroot>/var/lib/igos/pkm.db)")
    ap.add_argument("--max-report", type=int, default=40,
                    help="max rows printed per class")
    args = ap.parse_args()

    if not args.chroot.is_dir():
        print(f"[{PROG}] FATAL: chroot not found: {args.chroot}", file=sys.stderr)
        return 2
    db_path = args.db or (args.chroot / "var/lib/igos/pkm.db")
    if not db_path.is_file():
        print(f"[{PROG}] FATAL: pkm database not found: {db_path}", file=sys.stderr)
        return 2
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows, red, info = judge(conn, args.chroot)
    except sqlite3.Error as e:
        print(f"[{PROG}] FATAL: cannot read {db_path}: {e}", file=sys.stderr)
        return 2
    finally:
        try:
            conn.close()
        except Exception:
            pass

    total_red = sum(len(v) for v in red.values())
    print(f"[{PROG}] {len(rows)} configuration baseline(s) read from {db_path}; "
          f"{total_red} RED")
    for group in sorted(info):
        entries = info[group]
        print(f"  -- {group}: {len(entries)}")
        for entry in entries[:args.max_report]:
            print(f"     /{entry}")
        if len(entries) > args.max_report:
            print(f"     ... and {len(entries) - args.max_report} more")
    if not total_red:
        print(f"[{PROG}] PASS — every baseline is the stock its owner registered")
        return 0
    print(f"[{PROG}] FAIL — {total_red} baseline(s) are not the registered stock:")
    for group in sorted(red):
        entries = red[group]
        print(f"  == {group}: {len(entries)} ==")
        for entry in entries[:args.max_report]:
            print(f"    /{entry}")
        if len(entries) > args.max_report:
            print(f"    ... and {len(entries) - args.max_report} more")
    print(f"[{PROG}] Disposition: in the root, prove the live bytes are the "
          "registered stock (sha256), then `pkm refresh-baseline <path>`; a "
          "pruned package whose baseline is stale leaves its stock file on "
          "the image as an unowned file.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

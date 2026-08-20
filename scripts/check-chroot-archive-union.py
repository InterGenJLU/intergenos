#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed chroot-vs-archive-union coverage gate (build pre-flight).

Decided 2026-08-13, binding on every build after R001: the build refuses to
launch on a populated substrate until every file in the built chroot is
carried by at least one sealed archive OR is explicitly accounted for. The
class this kills: a file present in the build chroot that no archive
carries. The chroot is what gets evaluated; the archives are what installs
actually consume — any file in the first but not the second means the
evaluated system silently differs from every installed system (first bite:
an sshd configuration stub present in the chroot but absent from the
openssh archive).

What this gate checks (direction 1, the union direction):
  chroot file population  ⊆  union(all sealed archive members) ∪ accounted

  "Accounted" =, in check order:
    1. member of any sealed archive in <chroot>/var/lib/igos/archives/
       (ALL archives — mirror-only included; the union is about build
       fidelity, not the shipping set)
    2. a reviewed allowlist entry with a mandatory reason
       (config/chroot-archive-union-allowlist.txt)
    3. otherwise -> VIOLATION, grouped by DB state:
         - "installed-DB-owned but absent from every archive" — the stub
           class: pkm recorded it installed, no archive carries it, so no
           install ever receives it. The highest-severity group.
         - "unowned file" / "unowned symlink" — in neither the union nor
           the DB.

What this gate does NOT check (owned elsewhere, by design):
  - content drift between a chroot file and its claiming archive
    (the pam_unix.so post-rebuild hash-drift sibling): covered by
    scripts/check-iso-metadata-sync.py, whose standalone pipeline-faithful
    firing is already mandatory pre-capture (framework §3.5).
  - archive members absent from the chroot (deployment direction):
    covered by preflight-bash-tier-currency.py + redeploy-banked-archives.py.
  - the shipping tree at squashfs time: check-squashfs-ownership.py
    (Step 4.85) — that gate proves manifest ownership of what ships; this
    gate proves ARCHIVE CARRIAGE of what was built. A file can pass 4.85
    (DB-owned) and fail here (no archive carries it) — that is exactly the
    defect this gate exists to catch.

Empty-chroot semantics: a substrate without a populated archives corpus
(pre-toolchain, from-scratch launch) has nothing to compare — the gate
SELF-SKIPS LOUDLY (exit 0 with an explicit SKIP line). The orchestrator
fires it fail-closed on RESUME_CONTEXT (populated-substrate) launches.

Exit 0 clean or loud-skip, 1 on violations, 2 on usage/setup errors.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sqlite3
import sys
import tarfile
import time
from collections import defaultdict
# Threads, not processes: gzip decompression releases the GIL (zlib), so
# threads parallelize the dominant cost — and a process pool cannot pickle
# functions from a path-loaded module under Python 3.14's forkserver
# default (measured red in this gate's own test suite before switching).
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Same out-of-scope set as the Step 4.85 ownership gate: pseudo-fs mounts,
# the source cache, and runtime-trash prefixes. mnt/intergenos and
# mnt/hot-storage are the build-tree copy and the trace/checkpoint share.
SKIP_TOP = {"sources", "proc", "sys", "dev", "run", "tmp"}
SKIP_PREFIX = ("var/cache/", "var/log/journal/", "var/tmp/")
SKIP_DIRS = {"mnt/intergenos", "mnt/hot-storage"}
SKIP_EXACT = {".igos-chroot-ownership-normalized", "root/.bash_history"}


def _load_allowlist_class():
    """Reuse the ownership gate's Allowlist engine (one parser, one set of
    pattern semantics, one malformed-entry refusal) instead of a drifting
    copy."""
    spec = importlib.util.spec_from_file_location(
        "squashfs_ownership_gate",
        Path(__file__).resolve().parent / "check-squashfs-ownership.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Allowlist


def _list_members(archive: str) -> tuple[str, list[str], str | None]:
    """Member paths of one sealed archive, normalized to chroot-relative
    form (leading './' stripped, no trailing slash). Returns
    (archive, members, error)."""
    members: list[str] = []
    try:
        with tarfile.open(archive, "r:gz") as tf:
            for m in tf:
                name = m.name
                if name.startswith("./"):
                    name = name[2:]
                name = name.strip("/")
                if name and name != ".PKGINFO":
                    members.append(name)
    except Exception as exc:  # a corrupt archive is a finding, not a crash
        return archive, [], f"{type(exc).__name__}: {exc}"
    return archive, members, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chroot", type=Path, default=Path("/mnt/igos"))
    ap.add_argument("--archives", type=Path, default=None,
                    help="sealed-archive dir "
                         "(default: <chroot>/var/lib/igos/archives)")
    ap.add_argument("--db", type=Path, default=None,
                    help="pkm database (default: <chroot>/var/lib/igos/pkm.db)")
    ap.add_argument("--allowlist", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=min(12, os.cpu_count() or 4),
                    help="parallel archive-listing workers")
    ap.add_argument("--max-report", type=int, default=25,
                    help="max violations printed per group")
    args = ap.parse_args()

    if not args.chroot.is_dir():
        print(f"FATAL: chroot not found: {args.chroot}", file=sys.stderr)
        return 2
    if not args.allowlist.is_file():
        print(f"FATAL: allowlist not found: {args.allowlist}", file=sys.stderr)
        return 2
    archives_dir = args.archives or (args.chroot / "var/lib/igos/archives")

    archives = sorted(str(p) for p in archives_dir.glob("*.igos.tar.gz")) \
        if archives_dir.is_dir() else []
    if not archives:
        # Pre-toolchain / from-scratch: nothing to compare against. Loud,
        # never silent — a populated-substrate caller must treat this line
        # in its log as a defect if it ever appears there.
        print(f"[chroot-archive-union] SKIP — no sealed archives at "
              f"{archives_dir}; gate is meaningful only on a populated "
              f"substrate (empty chroot / pre-toolchain launch)")
        return 0

    Allowlist = _load_allowlist_class()
    allow = Allowlist(args.allowlist)

    db_path = args.db or (args.chroot / "var/lib/igos/pkm.db")
    db_owned: set[str] = set()
    if db_path.is_file():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        db_owned = {p.strip("/")
                    for (p,) in conn.execute("SELECT path FROM files")}
        conn.close()

    t0 = time.monotonic()
    union: set[str] = set()
    unreadable: list[str] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for archive, members, err in pool.map(_list_members, archives,
                                              chunksize=4):
            if err:
                unreadable.append(f"{os.path.basename(archive)}: {err}")
            union.update(members)
    listing_s = time.monotonic() - t0

    violations: dict[str, list[str]] = defaultdict(list)
    for entry in unreadable:
        violations["unreadable sealed archive"].append(entry)

    scanned = 0
    for root, dirs, files in os.walk(args.chroot):
        rel_root = os.path.relpath(root, args.chroot)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in SKIP_TOP]
            rel_root_posix = ""
        else:
            rel_root_posix = rel_root.replace(os.sep, "/")
            if (any((rel_root_posix + "/").startswith(p)
                    for p in SKIP_PREFIX)
                    or rel_root_posix in SKIP_DIRS):
                dirs[:] = []
                continue
        for f in files:
            rel = f"{rel_root_posix}/{f}" if rel_root_posix else f
            if rel in SKIP_EXACT:
                continue
            scanned += 1
            if rel in union:
                continue
            if allow.match(rel):
                continue
            if rel in db_owned:
                violations["installed-DB-owned but absent from every "
                           "archive (the stub class: no install receives "
                           "this file)"].append(rel)
            elif os.path.islink(os.path.join(root, f)):
                violations["unowned symlink (no archive, no DB row)"].append(rel)
            else:
                violations["unowned file (no archive, no DB row)"].append(rel)

    total = sum(len(v) for v in violations.values())
    print(f"[chroot-archive-union] {len(archives)} archives listed in "
          f"{listing_s:.0f}s ({args.jobs} workers); union {len(union)} "
          f"member paths; scanned {scanned} chroot files; DB paths "
          f"{len(db_owned)}")
    if not total:
        print("[chroot-archive-union] PASS — every built file is carried "
              "by a sealed archive or covered by a reviewed allowlist entry")
        return 0

    print(f"[chroot-archive-union] FAIL — {total} violation(s):")
    for group in sorted(violations):
        paths = sorted(violations[group])
        print(f"  == {group}: {len(paths)} ==")
        for p in paths[:args.max_report]:
            print(f"    /{p}")
        if len(paths) > args.max_report:
            print(f"    ... and {len(paths) - args.max_report} more")
    print("[chroot-archive-union] Disposition paths:")
    print("  stub class -> fix the owning recipe so the file lands in its")
    print("  archive (rebuild + reseal), or remove the stray from the chroot;")
    print("  unowned -> remove the stray, or add a REASONED allowlist entry")
    print("  (config/chroot-archive-union-allowlist.txt) if it is legitimate")
    print("  generated state no archive should ever carry.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

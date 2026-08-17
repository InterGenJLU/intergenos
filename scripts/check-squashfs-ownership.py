#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed squashfs ownership gate (build-squashfs Step 4.85).

Every file that will land in the squashfs must be traceable to an installed
package's manifest (the pkm database), to the package system's own state
rules, or to a reviewed allowlist entry carrying a reason. Unowned empty
directories fail the same way. Decided 2026-07-22: a live-ISO evaluation
read a pruned package's leftover directory skeleton as shipped payload, and
the archive corpus shipped mirror-only members for every prior candidate —
both are unowned-content classes this gate makes structurally impossible to
ship silently again.

Checks, in order, for every entry in the shipping tree (the chroot minus the
mksquashfs exclusions):

  1. path recorded by any installed package (either is_dir flag) -> OK
  2. var/lib/igos/archives/*  -> basename must be exactly
     "<name>-<version>.igos.tar.gz" for an INSTALLED (name, version), or be
     listed in --archive-excludes (mirror-only, excluded from the squashfs).
     Catches stale version twins, mirror leftovers, and unknown junk.
  3. var/lib/igos/packages/*  -> basename must be "<name>-<version>" of an
     installed row (a manifest for a package that is not installed is a
     removal/registration defect).
  4. var/lib/igos/helpers/<name>.manifest -> <name> must be installed.
  5. allowlist match (pattern + mandatory reason, reviewed in-tree) -> OK
  6. otherwise -> VIOLATION; any violation fails the build.

Reachability (added 2026-07-28): ownership proved a path SHOULD ship;
nothing proved a session could USE it. The flagship icon theme shipped
its root directory 0770 root:root on two candidates -- unreadable by any
non-root session, silently, because every check audited ownership and
never modes. Under the user-facing data trees (icons, themes, fonts,
pixmaps, applications) every directory must be other-traversable (o+rx)
and every file other-readable (o+r); these trees exist solely for
unprivileged sessions, so a stricter mode there is always a defect.
Symlinks are exempt (their own bits are meaningless); the allowlist
covers any reasoned exception.

Exit 0 clean, 1 on violations, 2 on usage/setup errors.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# Trees mksquashfs excludes (build-squashfs Step 5) or that are pseudo-fs
# mount points — they do not ship, so they are outside the gate's scope.
# NOTE: /mnt itself SHIPS (only mnt/intergenos is excluded — pruned in the
# walk below), so it stays in scope here.
SKIP_TOP = {"sources", "proc", "sys", "dev", "run", "tmp"}
SKIP_PREFIX = ("var/cache/", "var/log/journal/", "var/tmp/")
SKIP_EXACT = {".igos-chroot-ownership-normalized", "root/.bash_history"}

ARCHIVE_RE = re.compile(r"^(?P<base>.+)\.igos\.tar\.gz$")

# Data trees consumed by unprivileged sessions: everything here must be
# world-reachable or it cannot do the one thing it ships for.
USER_FACING_TREES = (
    "usr/share/icons/",
    "usr/share/themes/",
    "usr/share/fonts/",
    "usr/share/pixmaps/",
    "usr/share/applications/",
)


class Allowlist:
    """Pattern list with mandatory reasons. Three pattern forms:
    exact path, `dir/**` subtree prefix, fnmatch glob."""

    def __init__(self, path: Path):
        self.exact: set[str] = set()
        self.prefixes: list[str] = []
        self.globs: list[str] = []
        malformed: list[str] = []
        for i, raw in enumerate(path.read_text().splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\t+| {2,}", line, maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                malformed.append(f"line {i}: {raw!r}")
                continue
            pat = parts[0].strip()
            if pat.endswith("/**"):
                stem = pat[:-2]  # keep trailing slash
                if any(c in stem for c in "*?["):
                    # Glob + subtree combined (pip-*.dist-info/**): a
                    # startswith prefix would treat the glob chars as
                    # literals and never match — first live gate run
                    # proved it (33 false violations). fnmatch's * is
                    # not path-aware, so stem + '*' covers the subtree.
                    self.globs.append(stem + "*")
                else:
                    self.prefixes.append(stem)
            elif any(c in pat for c in "*?["):
                self.globs.append(pat)
            else:
                self.exact.add(pat)
        if malformed:
            # An exception without a reviewable reason is not an exception.
            print("FATAL: malformed allowlist entries (reason column "
                  "required):", file=sys.stderr)
            for m in malformed:
                print(f"  {m}", file=sys.stderr)
            raise SystemExit(2)

    def match(self, rel: str) -> bool:
        if rel in self.exact:
            return True
        for pfx in self.prefixes:
            if rel.startswith(pfx):
                return True
        return any(fnmatch.fnmatch(rel, g) for g in self.globs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chroot", type=Path, default=Path("/mnt/igos"))
    ap.add_argument("--db", type=Path, default=None,
                    help="pkm database (default: <chroot>/var/lib/igos/pkm.db)")
    ap.add_argument("--allowlist", type=Path, required=True)
    ap.add_argument("--archive-excludes", type=Path, default=None,
                    help="derive-iso-exclusions --mode=archive-excludes "
                         "output; listed archives are excluded from the "
                         "squashfs and therefore exempt")
    ap.add_argument("--max-report", type=int, default=25,
                    help="max violations printed per group")
    args = ap.parse_args()

    if not args.chroot.is_dir():
        print(f"FATAL: chroot not found: {args.chroot}", file=sys.stderr)
        return 2
    db_path = args.db or (args.chroot / "var/lib/igos/pkm.db")
    if not db_path.is_file():
        print(f"FATAL: pkm database not found: {db_path}", file=sys.stderr)
        return 2
    if not args.allowlist.is_file():
        print(f"FATAL: allowlist not found: {args.allowlist}", file=sys.stderr)
        return 2

    allow = Allowlist(args.allowlist)

    excluded_archives: set[str] = set()
    if args.archive_excludes and args.archive_excludes.is_file():
        for line in args.archive_excludes.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                excluded_archives.add(line)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    owned = {p.strip("/") for (p,) in conn.execute("SELECT path FROM files")}
    installed_nv = set()
    installed_names = set()
    for name, version in conn.execute("SELECT name, version FROM installed"):
        installed_nv.add(f"{name}-{version}")
        installed_names.add(name)
    conn.close()

    violations: dict[str, list[str]] = defaultdict(list)
    counts = {"files": 0, "dirs": 0}

    def check_special(rel: str) -> bool | None:
        """Returns True/False for the var/lib/igos special surfaces,
        None when the path is not one of them."""
        if rel.startswith("var/lib/igos/archives/"):
            if rel in excluded_archives:
                return True  # mirror-only: excluded from the squashfs
            m = ARCHIVE_RE.match(os.path.basename(rel))
            if m and m.group("base") in installed_nv:
                return True
            violations["archive not owned by an installed package "
                       "(stale twin, mirror leftover, or unknown)"].append(rel)
            return False
        if rel.startswith("var/lib/igos/packages/"):
            if os.path.basename(rel) in installed_nv:
                return True
            violations["manifest without a matching installed "
                       "package"].append(rel)
            return False
        if rel.startswith("var/lib/igos/helpers/"):
            base = os.path.basename(rel)
            if base.endswith(".manifest") and base[:-9] in installed_names:
                return True
            violations["helper manifest without a matching installed "
                       "package"].append(rel)
            return False
        return None

    for root, dirs, files in os.walk(args.chroot):
        rel_root = os.path.relpath(root, args.chroot)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in SKIP_TOP]
            rel_root_posix = ""
        else:
            rel_root_posix = rel_root.replace(os.sep, "/")
            # mnt/intergenos + mnt/hot-storage mirror the mksquashfs -e set
            # (the build tree bind and the trace/checkpoint share — trace
            # writes land in the chroot dir whenever the bind is absent,
            # and the ge9b-08 squashfs shipped 221 such trace files).
            if (any((rel_root_posix + "/").startswith(p)
                    for p in SKIP_PREFIX)
                    or rel_root_posix in ("mnt/intergenos",
                                          "mnt/hot-storage")):
                dirs[:] = []
                continue
        # Reachability: user-facing directories must be o+rx. Checked on
        # the directory's own walk visit, before the per-file loop.
        if rel_root_posix and (rel_root_posix + "/").startswith(
                USER_FACING_TREES):
            mode = os.lstat(root).st_mode
            if not os.path.islink(root) and (mode & 0o005) != 0o005:
                if not allow.match(rel_root_posix):
                    violations[
                        "user-facing path not world-reachable (mode)"
                    ].append(f"{rel_root_posix} [dir {oct(mode & 0o7777)}]")
        for f in files:
            rel = f"{rel_root_posix}/{f}" if rel_root_posix else f
            if rel in SKIP_EXACT or f.startswith("gid_Module_"):
                continue
            counts["files"] += 1
            if rel.startswith(USER_FACING_TREES):
                fpath = os.path.join(root, f)
                if not os.path.islink(fpath):
                    fmode = os.lstat(fpath).st_mode
                    if (fmode & 0o004) != 0o004 and not allow.match(rel):
                        violations[
                            "user-facing path not world-reachable (mode)"
                        ].append(f"{rel} [file {oct(fmode & 0o7777)}]")
            if rel in owned:
                continue
            special = check_special(rel)
            if special is not None:
                continue
            if allow.match(rel):
                continue
            kind = ("unowned symlink"
                    if os.path.islink(os.path.join(root, f))
                    else "unowned file")
            violations[kind].append(rel)
        # Empty-directory check (the skeleton class): a directory with no
        # entries that no installed package records and no allowlist entry
        # covers has no reason to ship.
        if rel_root_posix and not dirs and not files:
            counts["dirs"] += 1
            if (rel_root_posix not in owned
                    and not allow.match(rel_root_posix)):
                violations["unowned empty directory"].append(rel_root_posix)

    total = sum(len(v) for v in violations.values())
    print(f"[squashfs-ownership] scanned {counts['files']} files; "
          f"owned-path set {len(owned)}; installed {len(installed_names)}; "
          f"excluded archives {len(excluded_archives)}")
    if not total:
        print("[squashfs-ownership] PASS — every shipping file traces to an "
              "installed package, pkm state rules, or a reviewed allowlist "
              "entry")
        return 0

    print(f"[squashfs-ownership] FAIL — {total} violation(s):")
    for group in sorted(violations):
        paths = sorted(violations[group])
        print(f"  == {group}: {len(paths)} ==")
        for p in paths[:args.max_report]:
            print(f"    /{p}")
        if len(paths) > args.max_report:
            print(f"    ... and {len(paths) - args.max_report} more")
    print("[squashfs-ownership] Disposition paths:")
    print("  fix the owning recipe's manifest, remove the stray from the chroot,")
    print("  or add a REASONED allowlist entry")
    print("  (config/squashfs-ownership-allowlist.txt) if the file is")
    print("  legitimate generated state.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

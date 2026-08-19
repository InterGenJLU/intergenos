#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Chapter-8 temp-toolchain residue sweep (chroot-build-ch8.sh step 8.86).

The LFS chapter 5-7 temporary toolchain installs files onto the live chroot
root. A final-system package's DESTDIR deploy overlays its own files on top
but never deletes anything, so every path the final recipe deliberately drops
keeps the temporary toolchain's copy for the life of the chroot. Two measured
instances: packages/core/python/build.sh removes idlelib and /usr/bin/idle3*
from its DESTDIR (InterGenOS builds Python without tkinter, so IDLE cannot
run) and the chapter-7 Python's copies stay; the final GCC installs its GDB
pretty-printers under /usr/share/gdb/auto-load/ and the chapter-6 libstdc++'s
loader stays at /usr/lib/libstdc++.so.*-gdb.py.

The residue is unowned content in the shipping tree. build-squashfs Step 4.85
fails closed on unowned files; the 2026-08-15 from-scratch build — the first
from-scratch run since that gate landed — stopped there with 166 findings, 162
of them this class, and they were removed by hand. A from-scratch build
rebuilds the residue every time, which is why the disposition belongs here.

HOW IT DECIDES. A pattern from the patterns file SELECTS a candidate path.
Ownership then decides its fate: a candidate that an installed package's text
manifest records is KEPT and reported (an owned match means the pattern is
wrong or a recipe changed — that has to be read, not silently skipped); a
candidate no manifest records is removed and named. The ownership set is read
from the same text manifests `pkm import` reads, so this sweep and the
build-squashfs ownership gate answer "who owns this path" the same way.

The sweep refuses to delete anything when it cannot read an ownership set: no
manifest directory, or a manifest directory holding no manifests, exits
non-zero without touching the root. A check that cannot see what it checks is
not a check.

Exit 0 clean, 1 on a refusal or a removal failure, 2 on usage/setup errors.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import sys
from pathlib import Path

# Same anchored suffix `pkm.database._parse_manifest_line` uses: manifest
# paths may contain whitespace, so the hash is matched at end of line rather
# than by splitting on the first space.
SHA256_SUFFIX_RE = re.compile(r" sha256:[0-9a-f]{64}$")

# Pseudo-filesystems and build-tree binds. Inside the chroot these are mount
# points, not chroot content: walking them is wrong (their contents belong to
# the host kernel or the build tree) and expensive. Same top-level set the
# squashfs ownership gate prunes, so both answer "what is chroot content" the
# same way; `sources` is on the list there and stays on it here because the
# fat builder's tarball tree is not shipping content either.
SKIP_TOP = {"sources", "proc", "sys", "dev", "run", "tmp"}
SKIP_DIRS = {"mnt/intergenos", "mnt/hot-storage"}


class Pattern:
    """One declared residue class: the pattern, its reason, and the three
    match forms the squashfs ownership allowlist also uses."""

    def __init__(self, raw: str, reason: str):
        self.raw = raw
        self.reason = reason
        self.subtree = raw.endswith("/**")
        stem = raw[:-2] if self.subtree else raw  # keep the trailing slash
        self.stem = stem
        self.is_glob = any(c in stem for c in "*?[")

    def matches(self, rel: str) -> bool:
        if self.subtree:
            # The subtree root itself and everything under it.
            root = self.stem.rstrip("/")
            if self.is_glob:
                return (fnmatch.fnmatch(rel, root)
                        or fnmatch.fnmatch(rel, self.stem + "*"))
            return rel == root or rel.startswith(self.stem)
        if self.is_glob:
            return fnmatch.fnmatch(rel, self.stem)
        return rel == self.stem


def load_patterns(path: Path) -> list[Pattern]:
    """Read `<pattern><tab-or-2+-spaces><reason>` lines. A pattern without a
    reason is refused: an undeclared deletion class is not reviewable."""
    patterns: list[Pattern] = []
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
        if pat.startswith("/"):
            malformed.append(f"line {i}: pattern must be root-relative: {raw!r}")
            continue
        patterns.append(Pattern(pat, parts[1].strip()))
    if malformed:
        print("FATAL: malformed residue patterns (reason column required, "
              "root-relative paths):", file=sys.stderr)
        for m in malformed:
            print(f"  {m}", file=sys.stderr)
        raise SystemExit(2)
    return patterns


def load_owned(pkg_db: Path) -> tuple[set[str], int]:
    """Union of every path recorded by every installed package's manifest.
    Returns (owned paths without a leading or trailing slash, manifest count).
    """
    owned: set[str] = set()
    manifests = 0
    for entry in sorted(pkg_db.iterdir()):
        if not entry.is_file():
            continue
        try:
            text = entry.read_text(errors="replace")
        except OSError as e:
            print(f"FATAL: cannot read manifest {entry}: {e}", file=sys.stderr)
            raise SystemExit(2)
        if "FILE LIST:" not in text:
            continue
        manifests += 1
        in_files = False
        for line in text.splitlines():
            if not in_files:
                if line.strip() == "FILE LIST:":
                    in_files = True
                continue
            if not line.strip():
                continue
            path = SHA256_SUFFIX_RE.sub("", line.rstrip("\n"))
            owned.add(path.strip("/"))
    return owned, manifests


def walk_candidates(root: Path, patterns: list[Pattern],
                    walk_errors: list[str]) -> dict[str, list[str]]:
    """Map each pattern's raw text to the root-relative paths it matches.

    Directories are matched too, and a matched directory is reported without
    descending into it: the whole subtree goes or stays together, and its
    members are accounted for under the directory rather than counted twice.
    Symlinks are never followed — os.walk with followlinks=False plus lexists
    semantics throughout, so a symlink is a candidate in its own right and
    removing it cannot reach what it points at.
    """
    found: dict[str, list[str]] = {p.raw: [] for p in patterns}

    def on_error(err: OSError) -> None:
        # os.walk swallows directory-read errors by default. A tree the sweep
        # could not read is a tree it cannot certify, so every failure is
        # collected and the run refuses rather than reporting a clean pass
        # over a partially-read root.
        walk_errors.append(f"{getattr(err, 'filename', '?')}: {err}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error,
                                                followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        if rel_dir == "":
            dirnames[:] = [d for d in dirnames if d not in SKIP_TOP]
        elif rel_dir in SKIP_DIRS:
            dirnames[:] = []
            continue
        pruned = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if rel in SKIP_DIRS:
                # Checked here as well as on entry: a candidate-matching
                # directory is never descended into, so a bind that matched a
                # pattern would be removed before the entry check saw it.
                pruned.append(d)
                continue
            hit = next((p for p in patterns if p.matches(rel)), None)
            if hit is not None:
                found[hit.raw].append(rel)
                pruned.append(d)
        # Do not descend into a directory that is itself a candidate.
        dirnames[:] = [d for d in dirnames if d not in pruned]
        for f in sorted(filenames):
            rel = f"{rel_dir}/{f}" if rel_dir else f
            hit = next((p for p in patterns if p.matches(rel)), None)
            if hit is not None:
                found[hit.raw].append(rel)
    return found


def remove_path(target: Path) -> None:
    """Remove one candidate. lexists/islink first so a symlink is unlinked,
    never followed into its target."""
    if target.is_symlink() or not target.is_dir():
        target.unlink()
    else:
        shutil.rmtree(target)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("/"),
                    help="filesystem root to sweep (default: /, i.e. the "
                         "chroot's own root when run inside it)")
    ap.add_argument("--package-db", type=Path,
                    default=Path("/var/lib/igos/packages"),
                    help="directory of installed-package text manifests")
    ap.add_argument("--patterns", type=Path,
                    default=Path("/mnt/intergenos/config/"
                                 "ch8-residue-patterns.txt"))
    ap.add_argument("--record", type=Path, default=None,
                    help="write every removed path to this file, one per line")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be removed; remove nothing")
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"FATAL: sweep root not found: {args.root}", file=sys.stderr)
        return 2
    if not args.patterns.is_file():
        print(f"FATAL: patterns file not found: {args.patterns}",
              file=sys.stderr)
        return 2
    if not args.package_db.is_dir():
        print(f"FATAL: package manifest directory not found: "
              f"{args.package_db}", file=sys.stderr)
        return 2

    patterns = load_patterns(args.patterns)
    if not patterns:
        print(f"FATAL: {args.patterns} declares no residue patterns",
              file=sys.stderr)
        return 2

    owned, manifest_count = load_owned(args.package_db)
    if manifest_count == 0:
        print(f"REFUSED: no package manifests in {args.package_db} — the "
              f"ownership set is unknown, so nothing is removed. A sweep that "
              f"cannot see ownership is not a check.")
        return 1

    walk_errors: list[str] = []
    found = walk_candidates(args.root, patterns, walk_errors)

    removed: list[str] = []
    kept_owned: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []
    zero_match: list[str] = []

    print(f"[ch8-residue-sweep] root {args.root}; {manifest_count} manifest(s) "
          f"read; owned-path set {len(owned)}; {len(patterns)} declared "
          f"pattern(s)")

    for p in patterns:
        hits = found[p.raw]
        if not hits:
            zero_match.append(p.raw)
            print(f"  == {p.raw}: matched nothing")
            continue
        owned_hits = [h for h in hits if h in owned]
        stray_hits = [h for h in hits if h not in owned]
        print(f"  == {p.raw}: {len(hits)} match(es), "
              f"{len(owned_hits)} recorded by an installed package, "
              f"{len(stray_hits)} stray")
        for h in owned_hits:
            kept_owned.append((p.raw, h))
        for h in stray_hits:
            target = args.root / h
            if args.dry_run:
                print(f"    would remove /{h}")
                removed.append(h)
                continue
            try:
                remove_path(target)
            except OSError as e:
                print(f"    ERROR removing /{h}: {e}", file=sys.stderr)
                failures.append((h, str(e)))
                continue
            print(f"    removed /{h}")
            removed.append(h)

    if kept_owned:
        # Every one of these, not a sample: an owned match is a defect in the
        # pattern or a change in the owning recipe, and the whole list is what
        # makes it diagnosable.
        print(f"[ch8-residue-sweep] {len(kept_owned)} candidate(s) KEPT — "
              f"recorded by an installed package, so the declared pattern no "
              f"longer describes only residue. Read these:")
        for raw, h in kept_owned:
            print(f"    /{h}   (pattern {raw})")

    if zero_match:
        print(f"[ch8-residue-sweep] {len(zero_match)} pattern(s) matched "
              f"nothing this run: {', '.join(zero_match)}")

    if args.record and removed and not args.dry_run:
        try:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text("\n".join(removed) + "\n")
            print(f"[ch8-residue-sweep] removal record: {args.record}")
        except OSError as e:
            print(f"ERROR: cannot write removal record {args.record}: {e}",
                  file=sys.stderr)
            failures.append((str(args.record), str(e)))

    if walk_errors:
        print(f"[ch8-residue-sweep] FAIL — {len(walk_errors)} director"
              f"{'y' if len(walk_errors) == 1 else 'ies'} could not be read, "
              f"so this root is NOT certified clean:")
        for e in walk_errors:
            print(f"    {e}")
        return 1

    if failures:
        print(f"[ch8-residue-sweep] FAIL — {len(failures)} removal(s) failed; "
              f"the chroot still carries residue the shipping-tree ownership "
              f"gate will refuse.")
        return 1

    verb = "would be removed" if args.dry_run else "removed"
    print(f"[ch8-residue-sweep] PASS — {len(removed)} stray path(s) {verb}, "
          f"{len(kept_owned)} owned match(es) kept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

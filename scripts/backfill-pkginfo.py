#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""backfill-pkginfo.py — in-build, post-python .PKGINFO backfill.

Companion to gen-pkginfo.py + the pkg_archive build-time emit. The early-Ch8
recipe-less core packages (glibc/binutils/gcc/m4/ncurses/sed/bison/grep/perl)
are archived BEFORE core/python is built in the Ch8 chroot, so pkg_archive's
python3-guarded gen-pkginfo call is skipped and those archives ship without a
.PKGINFO. Once python3 exists (end of Ch8) this stamps a well-formed .PKGINFO
into the MISSING ones so the build-squashfs Step 4.7 sweep finds every archive
self-describing.

Distinct from inject-pkginfo.py: inject is the post-build, hand-run LOUD
DETECTOR (a non-empty inject is a reported gate-escape, rc=1). This is the
in-build backfill where stamping the pre-python archives is EXPECTED — so an
injection is the success path here, not a defect. Acceptance criteria:
  - MISSING-ONLY / idempotent: skips any archive already carrying a well-formed
    .PKGINFO (>= pkgname/pkgver/pkgrel) — never re-emits, so a recipe-bearing
    archive's real tier can never be clobbered to core by this sweep.
  - FAIL-LOUD: any unparseable name or gen-pkginfo/repack failure aborts
    (nonzero exit) — never seal a metadata-less archive silently.
  - LOSSLESS REPACK: existing members stay byte-identical (decompress -> tar
    --append one new root-owned ./.PKGINFO -> recompress); the result has an
    IDENTICAL member set plus the new .PKGINFO.

Tier handling matches inject-pkginfo's classification:
  - recipe-less archive  -> gen-pkginfo --fallback-tier core
  - toolchain-tier recipe (dual-built glibc/m4/ncurses: the staged archive is
    the FINAL core build, not the cross build) -> --force-tier core
  - any other matched recipe -> use the recipe's real tier (no override)

Usage:
  backfill-pkginfo.py --archive-dir DIR [--repo-root R] [--fallback-tier core]
                      [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

SUFFIX = ".igos.tar.gz"
HERE = Path(__file__).resolve().parent
NAME_VER = re.compile(r"^(.+)-([0-9].*)$")  # greedy: last '-<digit>' boundary


def load_recipe_tiers(repo_root: Path) -> dict:
    """name: field -> tier (dir name). Mirrors inject-pkginfo.load_recipe_names."""
    tiers = {}
    for yml in sorted(repo_root.glob("packages/*/*/package.yml")):
        try:
            d = yaml.safe_load(yml.read_text()) or {}
        except Exception:
            continue
        n = d.get("name")
        if n:
            tiers[n] = yml.parent.parent.name
    return tiers


def wellformed_pkginfo(archive: Path) -> bool:
    """True iff the archive carries a .PKGINFO with >= pkgname/pkgver/pkgrel
    (the same check as pkg_archive 2A + build-squashfs Step 4.7)."""
    try:
        with tarfile.open(archive) as t:
            m = next((x for x in t.getmembers()
                      if x.name.endswith(".PKGINFO")), None)
            if m is None:
                return False
            f = t.extractfile(m)
            body = f.read().decode("utf-8", "replace") if f else ""
    except Exception:
        return False
    keys = {ln.split("=", 1)[0] for ln in body.splitlines() if "=" in ln}
    return {"pkgname", "pkgver", "pkgrel"} <= keys


def size_and_count(archive: Path) -> tuple[int, int]:
    """Installed size (regular-file bytes) + non-dir entry count from the tar
    listing — NO extraction, so existing members are never touched. Mirrors
    inject-pkginfo.size_and_count (GNU `tar -tzvf` columns)."""
    out = subprocess.run(["tar", "-tzvf", str(archive)],
                         capture_output=True, text=True).stdout
    total = count = 0
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        perms, sz = parts[0], parts[2]
        if perms.startswith("d"):
            continue
        count += 1
        if perms.startswith("-"):
            try:
                total += int(sz)
            except ValueError:
                pass
    return total, count


def stamp(archive: Path, name: str, version: str, repo_root: Path,
          fallback_tier: str, force_tier: str | None) -> bool:
    """Synthesize .PKGINFO via gen-pkginfo and lossless-append it. Returns
    False on any failure (caller fails loud)."""
    size, count = size_and_count(archive)
    with tempfile.TemporaryDirectory(dir=archive.parent) as tmp:
        tmp = Path(tmp)
        cmd = [
            sys.executable, str(HERE / "gen-pkginfo.py"),
            "--name", name, "--version", version, "--repo-root", str(repo_root),
            "--fallback-tier", fallback_tier,
            "--out", str(tmp / ".PKGINFO"),
            "--size", str(size), "--filecount", str(count),
        ]
        if force_tier:
            cmd += ["--force-tier", force_tier]
        if subprocess.run(cmd).returncode != 0:
            return False
        raw = tmp / "pkg.tar"
        with open(raw, "wb") as f:
            if subprocess.run(["gzip", "-dc", str(archive)],
                              stdout=f).returncode != 0:
                return False
        if subprocess.run(["tar", "--append", "--owner=root", "--group=root",
                           "-f", str(raw), "-C", str(tmp),
                           "./.PKGINFO"]).returncode != 0:
            return False
        with open(archive, "wb") as f:
            if subprocess.run(["gzip", "-c", str(raw)],
                              stdout=f).returncode != 0:
                return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parent.parent),
    )
    ap.add_argument("--fallback-tier", default="core")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    arch_dir = Path(args.archive_dir)
    repo_root = Path(args.repo_root)
    if not arch_dir.is_dir():
        sys.stderr.write(f"backfill-pkginfo: no archive dir at {arch_dir}\n")
        return 0  # nothing staged here yet is not an error for this helper

    tiers = load_recipe_tiers(repo_root)
    missing, unparsed, failed, stamped = [], [], [], 0
    for a in sorted(arch_dir.glob(f"*{SUFFIX}")):
        if wellformed_pkginfo(a):          # MISSING-ONLY: leave good ones be
            continue
        stem = a.name[:-len(SUFFIX)]
        m = NAME_VER.match(stem)
        if not m:
            unparsed.append(stem)
            continue
        name, version = m.group(1), m.group(2)
        # A staged archive whose only recipe is toolchain-tier is the dual-built
        # FINAL core build (toolchain temp-tools are never archived here) — force
        # its tier to core. Recipe-less -> fallback core. Any other recipe keeps
        # its real tier (never clobbered).
        force_tier = "core" if tiers.get(name) == "toolchain" else None
        missing.append((a, name, version, force_tier))

    print(f"backfill: {len(missing)} archive(s) lack a well-formed .PKGINFO")
    if missing:
        print("  ->", [n for _, n, _, _ in missing])
    if unparsed:
        # FAIL-LOUD: a name we can't parse can't be stamped — do not seal silently.
        sys.stderr.write(f"backfill-pkginfo: cannot parse name-version: {unparsed}\n")
        return 1
    if args.dry_run:
        print("[dry-run] no changes made.")
        return 0

    for a, name, version, force_tier in missing:
        if stamp(a, name, version, repo_root, args.fallback_tier, force_tier):
            stamped += 1
        else:
            failed.append(a.name)
    print(f"backfill: stamped {stamped} / {len(missing)}")
    if failed:
        # FAIL-LOUD: abort the build rather than seal a metadata-less archive.
        sys.stderr.write(f"backfill-pkginfo: FAILED to stamp: {failed}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

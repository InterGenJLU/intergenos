#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""inject-pkginfo.py — Stamp .PKGINFO into built archives that lack it, and
segregate non-publishable (toolchain / intermediate build-stage) archives.

Background: the legacy bash builder (pkg-functions.sh:pkg_archive) historically
did not write .PKGINFO, so core/base archives shipped metadata-less while the
Python builder's (desktop/ai/extra) carried it. pkm.repo.generate_index builds
the repo index FROM each archive's .PKGINFO, so the metadata-less archives would
be absent from the index (uninstallable from the mirror). This host-side pass
closes the gap for already-built archives by synthesizing .PKGINFO from each
package.yml recipe (via gen-pkginfo.py) and re-packing.

It also segregates archives that must NOT be published to a user mirror:
  - toolchain tier (LFS Ch5-7 cross-tools — build-only)
  - intermediate build-stage variants (-pass1/-pass2/-pass3/-tmp/-bootstrap)

Classification per archive (<name>-<version>.igos.tar.gz):
  EXCLUDE     intermediate name marker, or recipe tier == toolchain
  OK          publishable + already has .PKGINFO        (left untouched)
  INJECT      publishable + missing .PKGINFO            (synthesize + repack)
  UNMATCHED   no recipe whose name: matches             (reported, left as-is)

Usage:
  inject-pkginfo.py --archive-dir DIR --exclude-dir DIR [--repo-root R] [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

SUFFIX = ".igos.tar.gz"
INTERMEDIATE = re.compile(r"-(pass[123]|tmp|bootstrap)(-|$)")
HERE = Path(__file__).resolve().parent


def load_recipe_names(repo_root: Path) -> dict:
    """name: field -> tier."""
    names = {}
    for yml in sorted(repo_root.glob("packages/*/*/package.yml")):
        try:
            d = yaml.safe_load(yml.read_text()) or {}
        except Exception:
            continue
        n = d.get("name")
        if n:
            names[n] = yml.parent.parent.name
    return names


def split_name_version(stem: str, known: list) -> tuple:
    """Longest recipe name that the stem equals or starts with (name + '-')."""
    best = None
    for n in known:
        if stem == n or stem.startswith(n + "-"):
            if best is None or len(n) > len(best):
                best = n
    if best is None:
        return None, None
    return best, (stem[len(best) + 1:] if len(stem) > len(best) else "")


def has_pkginfo(archive: Path) -> bool:
    try:
        with tarfile.open(archive) as t:
            return any(m.name.endswith(".PKGINFO") for m in t.getmembers())
    except Exception:
        return False


def size_and_count(archive: Path) -> tuple:
    """Installed size (sum of regular-file bytes) + non-dir entry count, read
    from the tar listing — NO extraction, so original members are never touched.
    GNU `tar -tzv` columns: <perms> <owner/group> <size> <date> <time> <name>.
    """
    out = subprocess.run(["tar", "-tzvf", str(archive)],
                         capture_output=True, text=True).stdout
    total = 0
    count = 0
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        perms, sz = parts[0], parts[2]
        if perms.startswith("d"):
            continue  # directory
        count += 1
        if perms.startswith("-"):
            try:
                total += int(sz)
            except ValueError:
                pass
    return total, count


def inject(archive: Path, name: str, version: str, repo_root: Path,
           fallback_tier: str | None = None,
           force_tier: str | None = None) -> bool:
    """Append ./.PKGINFO to the archive, leaving every existing member
    byte-identical (preserves root:root ownership, setuid bits, xattr caps).
    Decompress -> tar --append the one new member -> recompress.

    force_tier overrides a matched recipe's tier: the INJECT_MIN bucket holds
    the recipe-less core packages AND the dual-built toolchain-recipe packages
    (glibc/m4/ncurses) whose STAGED archive is the final core build — both must
    stamp core, but --fallback-tier alone leaves the dual-built three as
    tier=toolchain (a recipe matched). force_tier='core' corrects that.
    """
    mtime = subprocess.run(
        ["date", "-u", "-d", f"@{int(archive.stat().st_mtime)}",
         "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True).stdout.strip()
    size, count = size_and_count(archive)
    with tempfile.TemporaryDirectory(dir=archive.parent) as tmp:
        tmp = Path(tmp)
        # 1. synthesize .PKGINFO (size/filecount from the listing, no extract)
        cmd = [
            sys.executable, str(HERE / "gen-pkginfo.py"),
            "--name", name, "--version", version, "--repo-root", str(repo_root),
            "--out", str(tmp / ".PKGINFO"), "--builddate", mtime,
            "--size", str(size), "--filecount", str(count),
        ]
        if fallback_tier:
            cmd += ["--fallback-tier", fallback_tier]
        if force_tier:
            cmd += ["--force-tier", force_tier]
        if subprocess.run(cmd).returncode != 0:
            return False
        # 2. decompress -> append ./.PKGINFO (root-owned) -> recompress
        raw = tmp / "pkg.tar"
        with open(raw, "wb") as f:
            if subprocess.run(["gzip", "-dc", str(archive)], stdout=f).returncode != 0:
                return False
        if subprocess.run(["tar", "--append", "--owner=root", "--group=root",
                           "-f", str(raw), "-C", str(tmp), "./.PKGINFO"]).returncode != 0:
            return False
        with open(archive, "wb") as f:
            if subprocess.run(["gzip", "-c", str(raw)], stdout=f).returncode != 0:
                return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--exclude-dir", required=True)
    ap.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parent.parent),
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo_root = Path(args.repo_root)
    arch_dir = Path(args.archive_dir)
    excl_dir = Path(args.exclude_dir)
    names = load_recipe_names(repo_root)
    known = list(names)

    buckets = {"EXCLUDE": [], "OK": [], "INJECT": [], "INJECT_MIN": [],
               "UNMATCHED": []}
    for a in sorted(arch_dir.glob(f"*{SUFFIX}")):
        stem = a.name[:-len(SUFFIX)]
        if INTERMEDIATE.search(stem):
            buckets["EXCLUDE"].append((a, "intermediate"))
            continue
        name, version = split_name_version(stem, known)
        if name is None:
            # Recipe-less core package (LFS-Ch8 hardcoded in bash; no
            # package.yml). Parse name/version off the filename and stamp a
            # minimal core .PKGINFO so it's still indexed/installable.
            if has_pkginfo(a):              # idempotent: already stamped
                buckets["OK"].append((a, stem))
            else:
                m = re.match(r"^(.+?)-(\d.*)$", stem)
                if m:
                    buckets["INJECT_MIN"].append((a, m.group(1), m.group(2)))
                else:
                    buckets["UNMATCHED"].append((a, stem))
            continue
        if has_pkginfo(a):
            buckets["OK"].append((a, name))
        elif names.get(name) == "toolchain":
            # Dual-built package (LFS toolchain Ch5 + final Ch8). The STAGED
            # archive is the FINAL core build — toolchain temp-tools are NOT
            # archived to the package dir, so anything here is publishable. Its
            # ONLY recipe is toolchain-tier (whose tier/deps describe the cross
            # build, not the final), so stamp minimal core like the recipe-less
            # core packages (glibc/m4/ncurses/…). NOT excluded.
            buckets["INJECT_MIN"].append((a, name, version))
        else:
            buckets["INJECT"].append((a, name, version))

    print(f"EXCLUDE    : {len(buckets['EXCLUDE'])}")
    print(f"OK         : {len(buckets['OK'])}")
    print(f"INJECT     : {len(buckets['INJECT'])}")
    print(f"INJECT_MIN : {len(buckets['INJECT_MIN'])} (recipe-less core; minimal .PKGINFO)")
    if buckets["INJECT_MIN"]:
        print("  minimal:", [n for _, n, _ in buckets["INJECT_MIN"]])
    print(f"UNMATCHED  : {len(buckets['UNMATCHED'])}")
    if buckets["UNMATCHED"]:
        print("  unmatched:", [s for _, s in buckets["UNMATCHED"]])
    if args.dry_run:
        print("\n[dry-run] no changes made.")
        return 0

    excl_dir.mkdir(parents=True, exist_ok=True)
    for a, why in buckets["EXCLUDE"]:
        shutil.move(str(a), str(excl_dir / a.name))
    failed = []
    for a, name, version in buckets["INJECT"]:
        if not inject(a, name, version, repo_root):
            failed.append(a.name)
    for a, name, version in buckets["INJECT_MIN"]:
        if not inject(a, name, version, repo_root, fallback_tier="core",
                      force_tier="core"):
            failed.append(a.name)
    done = len(buckets["INJECT"]) + len(buckets["INJECT_MIN"]) - len(failed)
    print(f"\nexcluded -> {excl_dir}: {len(buckets['EXCLUDE'])}")
    print(f"injected: {done} / {len(buckets['INJECT']) + len(buckets['INJECT_MIN'])}")
    if failed:
        print("INJECT FAILURES:", failed)
        return 1
    # PI-12 loud detector (ruling 3): post-Block-A, pkg_archive's build-time
    # assertion (+ the build-squashfs Step 4.7 sweep) guarantee every archive
    # ships a well-formed .PKGINFO, so this publish-path backstop should find
    # NOTHING to inject. If it did, an archive escaped the build-time gate — it
    # was repacked here (the backstop keeps the mirror installable) but that is
    # itself a reported defect, not a silent fix: fail LOUDLY so the escape is
    # investigated rather than masked.
    if done > 0:
        sys.stderr.write(
            f"\n*** PI-12 GATE ESCAPE: {done} archive(s) reached the publish "
            f"backstop without a build-time .PKGINFO. They were repacked, but "
            f"the build-time assertion (pkg_archive / build-squashfs Step 4.7) "
            f"should have caught this. Investigate the build path. ***\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

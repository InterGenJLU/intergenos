#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""gen-pkginfo.py — Synthesize a canonical .PKGINFO from a package.yml recipe.

Single source of truth for the .PKGINFO key=value format (the same format the
Python builder emits at igos-build/tracker.py). Two consumers:

  1. scripts/pkg-functions.sh:pkg_archive() — the legacy bash builder calls this
     before `tar` so core/base archives ship .PKGINFO natively (the forward fix;
     previously only the Python builder wrote it, leaving core/base metadata-less).
  2. scripts/inject-pkginfo.sh — retroactively stamps .PKGINFO into already-built
     archives that predate the forward fix.

Format (lowercase Arch-style keys; matches igos-build/tracker.py + pkm
_parse_pkginfo at pkm/repo.py):
    pkgname / pkgver / pkgrel / pkgdesc / license / tier / builddate /
    size (installed bytes) / filecount / depend=<runtime dep> (one per line) /
    eula_helper (optional)

Usage:
    gen-pkginfo.py --name NAME --version VER --files-dir DIR [--repo-root R]
                   [--out FILE] [--builddate ISO8601]

Writes DIR/.PKGINFO by default (so the next `tar -C DIR -czf … .` packs it),
or to --out. size + filecount are computed from --files-dir; the remaining
fields come from the matching packages/*/NAME/package.yml. Exits non-zero
(without writing) if no recipe matches NAME — callers decide whether that's
fatal.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("gen-pkginfo: PyYAML not available\n")
    sys.exit(3)


def find_recipe(repo_root: Path, name: str) -> dict | None:
    """Locate the package.yml whose `name:` field == name. Returns parsed dict."""
    for yml in sorted(repo_root.glob("packages/*/*/package.yml")):
        try:
            data = yaml.safe_load(yml.read_text()) or {}
        except Exception:
            continue
        if data.get("name") == name:
            data["_tier"] = yml.parent.parent.name  # packages/<tier>/<pkg>/
            return data
    return None


def find_ships_as_recipe(repo_root: Path, name: str, version: str) -> dict | None:
    """Locate the recipe that declares `ships_as: <name>`, version-checked.

    Resolved BEFORE the exact-name lookup: for glibc/m4/ncurses a plain-name
    TOOLCHAIN recipe coexists with the ch8 `<name>-core` twin, so the
    exact-name match returns the cross build's recipe and stamps ITS
    release/tier onto an archive that is in fact the final ch8 build (every
    archive this tool stamps is — the pre-PyYAML guard keeps the ch5/ch7
    passes out of this path, and the toolchain temp-tools are never archived).
    Found 2026-07-30: the glibc archive re-sealed as pkgrel=1/tier=toolchain
    while the glibc-core recipe stood at release 4, masking the bump from
    every downstream (version,release) comparison — the same class the
    `-core` alias closed for recipes WITHOUT a plain-name collision. The
    version equality is a checked match, same contract as that alias.
    """
    for yml in sorted(repo_root.glob("packages/*/*/package.yml")):
        try:
            data = yaml.safe_load(yml.read_text()) or {}
        except Exception:
            continue
        if (data.get("ships_as") == name
                and str(data.get("version")) == str(version)):
            data["_tier"] = yml.parent.parent.name  # packages/<tier>/<pkg>/
            return data
    return None


def compute_size_and_count(files_dir: Path) -> tuple[int, int]:
    """Installed size (sum of regular-file bytes) + file count, under files_dir."""
    total = 0
    count = 0
    for root, _dirs, files in os.walk(files_dir):
        for f in files:
            p = Path(root) / f
            if p.is_symlink():
                count += 1
                continue
            try:
                total += p.stat().st_size
                count += 1
            except OSError:
                pass
    return total, count


def build_pkginfo(recipe: dict, name: str, version: str,
                  size: int, filecount: int, builddate: str) -> str:
    deps = []
    d = recipe.get("dependencies") or {}
    if isinstance(d, dict):
        deps = d.get("runtime") or []
    lines = [
        f"pkgname={name}",
        f"pkgver={version}",
        f"pkgrel={recipe.get('release', 1)}",
        f"pkgdesc={recipe.get('description', '')}",
        f"license={recipe.get('license', '')}",
        f"tier={recipe.get('_tier', '')}",
        f"builddate={builddate}",
        f"size={size}",
        f"filecount={filecount}",
    ]
    for dep in deps:
        lines.append(f"depend={dep}")
    # 3.0-F28 activation semantics (bash-tier emission half; the python-tier
    # archiver emits it in igos-build/tracker.py). Emitted only when the
    # recipe declares it, so pre-F28 archives and live-activating packages
    # carry no key — pkm._parse_pkginfo treats absence as False. Found
    # missing at the Q9 kernel leg: the F28 chain landed the tracker.py
    # emission only, so linux-kernel/-pass2 (bash-tier, the advisory's
    # primary subjects) shipped without the flag.
    if recipe.get("reboot_required"):
        lines.append("reboot_required=true")
    if recipe.get("eula_helper"):
        lines.append(f"eula_helper={recipe['eula_helper']}")
    # pkm 2b: a package that downloads proprietary software declares its vendor
    # license here. pkm reads it from the archive .PKGINFO to recognize a
    # download-helper package and route `pkm install <app>` through the
    # continue-prompt + helper flow (the operator-specified detection signal).
    if recipe.get("payload_license"):
        lines.append(f"payload_license={recipe['payload_license']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--files-dir", default=None,
                    help="Directory of the package's staged/extracted files "
                         "(size + filecount computed from it). Omit when "
                         "--size and --filecount are supplied directly.")
    ap.add_argument("--size", type=int, default=None,
                    help="Installed size in bytes (overrides --files-dir walk)")
    ap.add_argument("--filecount", type=int, default=None,
                    help="File count (overrides --files-dir walk)")
    ap.add_argument("--repo-root", default=os.environ.get(
        "IGOS_REPO_ROOT", str(Path(__file__).resolve().parent.parent)))
    ap.add_argument("--out", default=None,
                    help="Output path (default: <files-dir>/.PKGINFO)")
    ap.add_argument("--builddate", default=None,
                    help="ISO8601 builddate (default: now UTC)")
    ap.add_argument("--fallback-tier", default=None,
                    help="If no recipe matches, emit a MINIMAL .PKGINFO with "
                         "this tier (name/version/size only, no deps). For the "
                         "recipe-less LFS-Ch8 core packages built by hardcoded "
                         "bash logic (binutils/gcc/coreutils/…).")
    ap.add_argument("--force-tier", default=None,
                    help="Override the tier of a MATCHED recipe (other recipe "
                         "fields are kept). For dual-built LFS packages whose "
                         "ONLY recipe is toolchain-tier (glibc/m4/ncurses) but "
                         "whose STAGED archive is the FINAL core build — the "
                         "recipe's toolchain tier describes the cross build, not "
                         "the shipped artifact. Distinct from --fallback-tier "
                         "(which applies only when NO recipe matches).")
    args = ap.parse_args()

    repo_root = Path(args.repo_root)
    files_dir = Path(args.files_dir) if args.files_dir else None
    recipe = find_ships_as_recipe(repo_root, args.name, args.version)
    if recipe is None:
        recipe = find_recipe(repo_root, args.name)
    if recipe is None:
        # LFS ch8 dual-name convention: recipe `<name>-core`, shipped pkgname
        # `<name>` (16 packages: util-linux, gcc, coreutils, ...). The alias is
        # accepted ONLY when the recipe's version equals the staged version —
        # a checked match, not a suffix guess. Without this, those packages
        # fell into the recipe-less fallback below and stamped pkgrel=1
        # regardless of the recipe's release (masked util-linux's L29 r2 bump
        # from every downstream (version,release) comparison; caught GE-02).
        cand = find_recipe(repo_root, f"{args.name}-core")
        if cand is not None and str(cand.get("version")) == str(args.version):
            recipe = cand
    if recipe is None:
        if not args.fallback_tier:
            sys.stderr.write(f"gen-pkginfo: no recipe for '{args.name}'\n")
            return 2
        # Minimal fallback — recipe-less core package (K12 gap).
        recipe = {"release": 1, "description": "", "license": "",
                  "_tier": args.fallback_tier, "dependencies": {}}

    # --force-tier overrides the resolved tier (recipe's dir-derived _tier or
    # the fallback), keeping every other field. For the dual-built toolchain
    # packages whose shipped archive is the final core build.
    if args.force_tier:
        recipe["_tier"] = args.force_tier

    if args.size is not None and args.filecount is not None:
        size, count = args.size, args.filecount
    elif files_dir is not None:
        size, count = compute_size_and_count(files_dir)
    else:
        sys.stderr.write("gen-pkginfo: need --files-dir OR (--size and --filecount)\n")
        return 2
    builddate = args.builddate or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    content = build_pkginfo(recipe, args.name, args.version, size, count, builddate)

    if args.out:
        out = Path(args.out)
    elif files_dir is not None:
        out = files_dir / ".PKGINFO"
    else:
        sys.stderr.write("gen-pkginfo: need --out when --files-dir omitted\n")
        return 2
    out.write_text(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())

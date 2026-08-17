#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""preflight-bash-tier-currency.py — refuse a resume that would silently ship
a stale bash-tier build.

The Python tiers enforce currency per package via the skip-built template
hash. The bash-driven tiers (core / core-extra / base, plus the kernel) have
NO such layer: a bash-tier recipe that advanced after the substrate's archive
was sealed is invisible to the git delta AND to the build itself, so the
substrate's stale build ships silently. The manual tree-vs-archive sweep this
gate encodes reads each sealed archive's own ./.PKGINFO — the only honest
currency instrument; the database release column has been found corrupted
corpus-wide and is never consulted.

For every bash-tier recipe the gate compares tree (version, release) against
the newest banked archive in the chroot and derives the phase that builds the
package by reading the drivers' own run_package lines. A stale (or never
built) package is COVERED when the requested --start-at phase runs its
building phase, and the gate passes with the rebuild named; it REFUSES
(exit 1) when the resume would skip the phase — naming each package, its
building phase, and the minimal --start-at that covers everything.

Self-skips (exit 0, loudly) on an empty chroot: a from-scratch build rebuilds
everything by construction. Exit 2 = the gate could not verify (missing
inputs) — never a quiet pass.
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Shared helpers from the redeploy tool — one identity/currency reader, not two
# drifting copies (it in turn uses pkm's own version comparator).
_spec = importlib.util.spec_from_file_location(
    "redeploy_banked_archives", REPO_ROOT / "scripts" / "redeploy-banked-archives.py")
_rba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rba)

# Orchestrator phase order (cite scripts/build-intergenos.sh run_phase block;
# only the relative order of the package phases matters here).
PHASE_ORDER = ["validate", "verify-sources", "setup", "toolchain", "chroot-prep",
               "chroot-tools", "core", "config", "core-extra", "base", "kernel",
               "desktop", "extra", "compute", "ai", "bootloader", "image",
               "manifest", "squashfs", "ukis-verity", "iso"]

DRIVER_PHASES = [  # (driver script, phase its run_package lines build in)
    ("chroot-build-ch8.sh", "core"),
    ("chroot-build-core-extra.sh", "core-extra"),
    ("chroot-build-base.sh", "base"),
]

RUN_PACKAGE_RE = re.compile(r"^\s*run_package\s+\"?([A-Za-z0-9._+-]+)\"?")


def log(msg: str) -> None:
    print(f"[preflight-bash-tier-currency] {msg}")


def load_driver_map(scripts_dir: Path) -> "dict[str, str]":
    """Package dir-name -> building phase, from the drivers' run_package lines."""
    mapping: "dict[str, str]" = {}
    for script, phase in DRIVER_PHASES:
        path = scripts_dir / script
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = RUN_PACKAGE_RE.match(raw)
            if m:
                mapping.setdefault(m.group(1), phase)
    # phase_kernel builds exactly linux-kernel via the ch10 driver.
    mapping.setdefault("linux-kernel", "kernel")
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chroot", default="/mnt/igos")
    ap.add_argument("--packages-dir", default=str(REPO_ROOT / "packages"))
    ap.add_argument("--scripts-dir", default=str(REPO_ROOT / "scripts"))
    ap.add_argument("--start-at", default="",
                    help="The resume's --start-at phase (empty = full build)")
    args = ap.parse_args()

    chroot = Path(args.chroot)
    packages_dir = Path(args.packages_dir)
    archives_dir = chroot / "var/lib/igos/archives"

    if args.start_at and args.start_at not in PHASE_ORDER:
        log(f"SETUP ERROR: unknown --start-at phase '{args.start_at}'")
        return 2
    if not packages_dir.is_dir():
        log(f"SETUP ERROR: packages dir absent: {packages_dir}")
        return 2
    if not archives_dir.is_dir() or not any(archives_dir.glob("*.igos.tar.gz")):
        log("SKIP (clean): no banked archives — empty chroot / from-scratch "
            "build rebuilds everything by construction")
        return 0

    driver_map = load_driver_map(Path(args.scripts_dir))
    if not driver_map:
        log("SETUP ERROR: no run_package lines found in any bash driver — "
            f"wrong --scripts-dir ({args.scripts_dir})?")
        return 2

    recipes = _rba.load_recipes(packages_dir)

    # Newest banked archive per ship name (identity from .PKGINFO).
    newest: "dict[str, tuple]" = {}
    for arc in sorted(archives_dir.glob("*.igos.tar.gz")):
        info = _rba.read_pkginfo(arc)
        if not info or "pkgname" not in info:
            continue
        name = info["pkgname"]
        ver, rel = info.get("pkgver", "0"), int(info.get("pkgrel", "1") or 1)
        if name not in newest or _rba.vcompare(newest[name], (ver, rel)) < 0:
            newest[name] = (ver, rel)

    start_idx = PHASE_ORDER.index(args.start_at) if args.start_at else 0

    checked = 0
    covered_rebuilds, refusals = [], []
    for ship, recipe in sorted(recipes.items()):
        rel_path = Path(recipe["path"]).parent
        tier_dir = rel_path.parent.name
        if tier_dir not in ("core", "base"):
            continue
        pkg_dir = rel_path.name
        phase = driver_map.get(pkg_dir) or driver_map.get(recipe["recipe_name"])
        if phase is None:
            # Not wired into any bash driver — the tier-coverage gate owns
            # that class; skip here rather than double-report.
            continue
        checked += 1
        banked = newest.get(ship)
        tree = (recipe["version"], recipe["release"])
        if banked is not None and _rba.vcompare(banked, tree) >= 0:
            continue  # archive current (or ahead) — nothing owed
        state = "NEVER-BUILT" if banked is None else \
            f"stale (archive {banked[0]}-{banked[1]} < tree {tree[0]}-{tree[1]})"
        if PHASE_ORDER.index(phase) >= start_idx:
            covered_rebuilds.append((ship, phase, state))
        else:
            refusals.append((ship, phase, state))

    log(f"bash-tier packages checked: {checked} "
        f"(instrument: sealed-archive .PKGINFO, never the DB)")
    for ship, phase, state in covered_rebuilds:
        log(f"WILL REBUILD on this resume: {ship} [{state}] — builds in "
            f"phase '{phase}', covered by --start-at "
            f"{'<full build>' if not args.start_at else args.start_at}")
    if refusals:
        for ship, phase, state in refusals:
            log(f"REFUSE: {ship} [{state}] builds in phase '{phase}', which "
                f"--start-at {args.start_at} SKIPS — the stale build would ship silently")
        earliest = min((PHASE_ORDER.index(p) for _, p, _ in refusals))
        log(f"REFUSED: {len(refusals)} bash-tier package(s) stale and uncovered. "
            f"Cover them (--start-at {PHASE_ORDER[earliest]} or earlier, or "
            f"per-package IGOS_START_AT/IGOS_STOP_AFTER driver rebuilds) and re-run.")
        return 1

    log(f"PASS: no stale bash-tier build escapes this resume "
        f"({len(covered_rebuilds)} covered rebuild(s), 0 uncovered)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

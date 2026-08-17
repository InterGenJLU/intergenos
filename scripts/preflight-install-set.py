#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Pre-squashfs install-set audit — every staged .igos.tar.gz archive MUST be
seen by Forge's get_archives() parser and resolve into an installable tier.

This closes the gap the llama-cpp blocker exposed: the existing pre-squashfs
audit checks that declared verify_paths exist *in the chroot*, so an archive
that is physically present in the squashfs but that Forge's installer silently
never parses (a version that doesn't start with a digit, or two archives that
collide to the same package name) passes every chroot-side check while the
package never actually installs on the target. Of 826 archives, llama-cpp-b5545
was dropped exactly this way and shipped an AI with no inference engine.

This audit runs the REAL Forge parser (installer/backend/packages.get_archives)
against the staged archive directory and FAILS the build if any staged archive
is not yielded by it, or (with --strict-tier) resolves to no packages/<tier>/
directory so get_group_packages could never select it.

Exit codes:
  0 — every staged archive is parsed (and, with --strict-tier, tier-resolvable)
  1 — one or more archives are dropped / collide / orphaned
  2 — argument or environment error
"""

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from installer.backend import packages as P
except Exception as e:  # noqa: BLE001 — surface import failure as an env error
    sys.stderr.write(f"ERROR: cannot import installer.backend.packages: {e}\n")
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive-dir", required=True,
                    help="staged archive dir, e.g. /mnt/igos/var/lib/igos/archives")
    ap.add_argument("--packages-dir", default=str(REPO / "packages"),
                    help="packages/ tree for tier resolution (default: repo packages/)")
    ap.add_argument("--strict-tier", action="store_true",
                    help="also fail if a parsed archive maps to no packages/<tier>/ dir")
    args = ap.parse_args()

    adir = Path(args.archive_dir)
    if not adir.is_dir():
        sys.stderr.write(f"ERROR: archive dir not found: {adir}\n")
        return 2

    files = sorted(f for f in adir.iterdir() if f.name.endswith(".igos.tar.gz"))
    if not files:
        sys.stderr.write(f"ERROR: no .igos.tar.gz archives under {adir}\n")
        return 2

    # Run the REAL Forge parser — a regression in get_archives() is caught here.
    parsed = P.get_archives(adir)              # {name: (version, path)}
    parsed_paths = {str(p) for (_v, p) in parsed.values()}

    errors = []

    # 1. Completeness: every staged archive must be represented. A file missing
    #    from parsed_paths was either skipped (unparseable version) or clobbered
    #    by a later same-name archive — both are the silent-drop class.
    for f in files:
        if str(f) not in parsed_paths:
            errors.append(
                f"DROPPED: {f.name} is staged but Forge get_archives() never "
                f"yields it (version-parse miss or duplicate-name clobber) -> "
                f"present in the image, never installed")

    if len(parsed) < len(files):
        errors.append(
            f"COUNT: {len(files)} archives staged but get_archives() returned "
            f"{len(parsed)} unique names (delta={len(files) - len(parsed)} lost)")

    # 2. Tier resolution: a parsed archive that maps to no packages/<tier>/ dir
    #    can never be put in any group's tier set, so get_group_packages would
    #    never select it even though it parses.
    if args.strict_tier:
        pdir = Path(args.packages_dir)
        names_in_tiers = set()
        for tdir in sorted(pdir.iterdir()):
            if not tdir.is_dir():
                continue
            for pd in tdir.iterdir():
                if pd.is_dir():
                    for cand in P._archive_name_candidates(pd.name):
                        names_in_tiers.add(cand)
        for name in sorted(parsed):
            if name not in names_in_tiers:
                errors.append(
                    f"TIER-ORPHAN: archive {name!r} resolves to no "
                    f"packages/<tier>/ dir -> get_group_packages can never "
                    f"select it")

    if errors:
        sys.stderr.write("install-set audit FAILED:\n")
        for e in errors:
            sys.stderr.write(f"  - {e}\n")
        return 1

    print(f"install-set audit OK: {len(files)} staged archives all parsed by "
          f"Forge get_archives() ({len(parsed)} unique names"
          f"{', all tier-resolvable' if args.strict_tier else ''}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

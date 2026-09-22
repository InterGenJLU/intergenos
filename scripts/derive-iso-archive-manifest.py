#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Derive the ISO archive manifest: the full manifest minus the mirror-only
archives the ISO does not carry.

    derive-iso-archive-manifest.py \\
        --full-manifest    build/intergenos-archive-manifest.txt \\
        --archive-excludes build/iso-mirror-archive-excludes.txt \\
        --output           build/intergenos-archive-manifest-iso.txt

The full manifest is phase_manifest's census of every archive in the build
chroot — correct for the mirror, which ships all of them. build-squashfs keeps
the mirror-only archives off the ISO (derive-iso-exclusions
--mode=archive-excludes), so the manifest the ISO carries must list only what
the ISO ships; the installer's integrity check refuses a media that promises
archives it does not carry (the R001.2 install abort, 2026-08-27).

Both manifests are signed in the same ceremony (scripts/sign-manifest.sh).
build-squashfs Step 4.8 stages THIS one into /install/ under the canonical
installer name; the mirror keeps the full one.

Exit 0 on success (counts on stderr). Exit 1 and no output file on a
malformed full manifest, an unreadable exclusion list, or a derivation that
would leave zero entries.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import manifest_coverage as mc  # noqa: E402

PROG = "derive-iso-archive-manifest"


def main() -> int:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full-manifest", required=True, type=Path)
    ap.add_argument("--archive-excludes", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    try:
        full_text = args.full_manifest.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[{PROG}] error: cannot read full manifest: {e}", file=sys.stderr)
        return 1
    try:
        excludes = mc.read_excludes(args.archive_excludes)
        blocks = mc.read_exclude_blocks(args.archive_excludes)
    except OSError as e:
        print(f"[{PROG}] error: cannot read exclusion list: {e}", file=sys.stderr)
        return 1
    try:
        r = mc.derive_iso_manifest(full_text, excludes)
    except ValueError as e:
        print(f"[{PROG}] error: {e}", file=sys.stderr)
        return 1

    tmp = args.output.with_name(args.output.name + ".tmp")
    tmp.write_text(r.text, encoding="utf-8")
    tmp.replace(args.output)

    print(f"[{PROG}] full manifest:  {args.full_manifest} "
          f"({r.kept + len(r.dropped)} entries)", file=sys.stderr)
    print(f"[{PROG}] exclusion list: {args.archive_excludes} "
          f"({len(excludes)} archive names declared)", file=sys.stderr)
    print(f"[{PROG}] ISO manifest:   {args.output} — kept {r.kept}, "
          f"excluded {len(r.dropped)}", file=sys.stderr)
    # One line per declared mirror package that has no archive under ANY of
    # its names. The exclusion list names each package once per shape its
    # archive may carry, so a name absent from the census is normal when the
    # package was built under its other name; counting names would report
    # every built package too.
    present = set(r.dropped)
    unbuilt = [label for label, names in blocks
               if not any(name in present for name in names)]
    if unbuilt:
        print(f"[{PROG}] note: {len(unbuilt)} declared mirror package(s) have "
              f"no archive in the full manifest under any of their names "
              f"(declared mirror packages with no built archive):",
              file=sys.stderr)
        for label in unbuilt:
            print(f"    {label}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-corpus-correspondence.py — staged-corpus <-> built-corpus byte gate.

Decided 2026-08-21: a release publish derives its staging corpus from the
evaluated build corpus and must PROVE byte identity before the index is
generated and signed. The overlay-onto-persistent-staging model is retired
as a publish input: it silently served stale bytes for every package whose
(version, release) did not move across a full rebuild — 796 of 842 published
components after the first full-rebuild release, discovered only because the
release's SBOM was compared against served bytes.

The gate checks BOTH directions, fail-closed:

  1. Every publishable archive in the built corpus is present in staging
     with an identical sha256 (the served-stale class).
  2. Every archive in staging exists in the built corpus with an identical
     sha256 (the orphaned-baseline class — nothing rides from a prior
     staging generation).

"Publishable" excludes the never-published build intermediates. The
exclusion set is DERIVED and PRINTED, never hidden: an archive is excluded
iff its package name ends in -pass<N>, -tmp, or -bootstrap. Everything else
must correspond. (The gate's first real firing, 2026-08-21, proved the name
patterns match the built corpus's intermediate set exactly — 20/20 — and
that a toolchain-tier recipe-directory derivation over-matches: glibc, m4,
and ncurses carry toolchain twin recipes while their plain archives publish,
the Chapter-8 recipe-less class. An unanticipated future intermediate shape
fails loud as MISSING-from-staging rather than slipping through.)

Inputs:
  --staging DIR          the staging archive dir (the corpus about to be
                         indexed and signed).
  --chroot-manifest FILE sha256sum output taken INSIDE the build chroot's
                         archive dir (the evaluated corpus's own bytes):
                           ssh <builder>@<build-vm> \
                             'cd /var/lib/igos/archives 2>/dev/null \
                              || cd /mnt/igos/var/lib/igos/archives; \
                              sudo sha256sum *.igos.tar.gz'
Exit 0: full correspondence. Exit 2: findings (each named). Exit 1: usage /
unreadable input. There is deliberately NO bypass flag: a publish that
cannot prove correspondence does not publish.
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

INTERMEDIATE_RE = re.compile(r"^(?P<base>.+?)-(pass\d+|tmp|bootstrap)$")


def archive_pkgname(filename: str) -> str:
    """<name>-<version>.igos.tar.gz -> <name> (version = trailing dotted run)."""
    stem = filename[: -len(".igos.tar.gz")]
    m = re.match(r"^(?P<name>.+)-(?P<ver>[0-9][0-9A-Za-z.+_]*)$", stem)
    return m.group("name") if m else stem


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> dict:
    """Parse sha256sum output -> {archive filename: sha256}."""
    entries = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            print(f"ERROR: unparseable manifest line: {line!r}", file=sys.stderr)
            sys.exit(1)
        name = parts[1].lstrip("*./")
        if name.endswith(".igos.tar.gz"):
            entries[name] = parts[0]
    if not entries:
        print(f"ERROR: no archive entries in manifest {path}", file=sys.stderr)
        sys.exit(1)
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description="staged<->built corpus byte gate")
    ap.add_argument("--staging", required=True, type=Path)
    ap.add_argument("--chroot-manifest", required=True, type=Path)
    args = ap.parse_args()

    for p, what in ((args.staging, "staging dir"), (args.chroot_manifest, "manifest")):
        if not p.exists():
            print(f"ERROR: {what} not found: {p}", file=sys.stderr)
            return 1

    built = load_manifest(args.chroot_manifest)
    staged = {p.name: p for p in sorted(args.staging.glob("*.igos.tar.gz"))}

    excluded = []
    publishable = {}
    for fname, digest in built.items():
        pkg = archive_pkgname(fname)
        if INTERMEDIATE_RE.match(pkg):
            excluded.append(fname)
            continue
        publishable[fname] = digest

    print(f"[corpus-gate] built corpus: {len(built)} archives "
          f"({len(publishable)} publishable, {len(excluded)} intermediates excluded)")
    for f in sorted(excluded):
        print(f"[corpus-gate]   excluded (never-publish intermediate): {f}")
    print(f"[corpus-gate] staging: {len(staged)} archives")

    findings = []

    for fname, want in sorted(publishable.items()):
        sp = staged.get(fname)
        if sp is None:
            findings.append(f"MISSING from staging (built, publishable, unstaged): {fname}")
            continue
        have = sha256_file(sp)
        if have != want:
            findings.append(
                f"BYTES DIFFER (staging serves other bytes than the built corpus): "
                f"{fname} staged={have[:16]}… built={want[:16]}…")

    for fname, sp in sorted(staged.items()):
        if fname in publishable:
            continue
        if fname in built:
            findings.append(f"STAGED INTERMEDIATE (never-publish archive in staging): {fname}")
        else:
            findings.append(
                f"ORPHAN in staging (absent from the built corpus — stale baseline riding): {fname}")

    if findings:
        print(f"[corpus-gate] FAIL — {len(findings)} finding(s):")
        for f in findings:
            print(f"[corpus-gate]   {f}")
        print("[corpus-gate] Remedy: re-stage the staging corpus from the evaluated "
              "chroot, regenerate the manifest, re-run. There is no bypass.")
        return 2

    matched = len(publishable)
    print(f"[corpus-gate] PASS — {matched}/{matched} publishable archives byte-identical "
          f"in both directions; staging carries no orphans.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

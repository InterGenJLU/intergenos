#!/usr/bin/env python3
# Derive a targeted-rebuild set EMPIRICALLY: every chroot archive whose
# .PKGINFO (version,release) mismatches the tree's package.yml is a member.
# Used for off-a-snapshot respins where the git delta cannot see what the
# reverted chroot actually holds (origin: the GE-02 respin, 2026-07-05).
#
# Name resolution honors the LFS ch8 dual-name convention: recipe
# `<name>-core`, shipped pkgname `<name>` (util-linux, gcc, coreutils, ...).
# Since 2026-07-21 (F25 namespace wave) the mapping is DECLARED per-recipe
# via the `ships_as:` package.yml field (single source, shared with
# igos-build/graph.py runtime-dep resolution) instead of guessed from the
# -core suffix. The version-equality guard stays as belt: the alias is
# accepted ONLY when the recipe's version equals the archive's version.
#
# FAIL-LOUD: an archive whose pkgname resolves to NO tree recipe is REPORTED
# (never silently skipped). The build/-era first version of this script
# skipped unmatched names — which hid the resurrected pre-L29 util-linux r1
# archive from the GE-02 respin derive ("0 mismatches" while one existed;
# caught by squashfs gate 4.76).
import glob
import os
import re
import sys
import tarfile

CHROOT_ARCHIVES = os.environ.get(
    "IGOS_ARCHIVES_DIR", "/mnt/igos/var/lib/igos/archives")
PACKAGES_GLOB = os.environ.get(
    "IGOS_PACKAGES_GLOB",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "packages", "*", "*", "package.yml"))

tree = {}
ship_map = {}  # ships_as name -> recipe name (declared, not suffix-guessed)
for y in glob.glob(PACKAGES_GLOB):
    name = ver = rel = ships_as = None
    for line in open(y, encoding="utf-8", errors="replace"):
        m = re.match(r"^name:\s*(\S+)", line)
        if m:
            name = m.group(1)
        m = re.match(r"^ships_as:\s*(\S+)", line)
        if m:
            ships_as = m.group(1)
        m = re.match(r"""^version:\s*["']?([^"'\s]+)""", line)
        if m:
            ver = m.group(1)
        m = re.match(r"^release:\s*(\d+)", line)
        if m:
            rel = m.group(1)
    if name and ver:
        tree[name] = (ver, rel or "1")
        if ships_as:
            if ships_as in ship_map:
                sys.exit(f"FATAL: duplicate ships_as '{ships_as}' "
                         f"({ship_map[ships_as]} and {name})")
            ship_map[ships_as] = name

mismatch = []
unmatched = []
seen = set()
for a in sorted(glob.glob(os.path.join(CHROOT_ARCHIVES, "*.igos.tar.gz"))):
    try:
        with tarfile.open(a) as t:
            f = t.extractfile("./.PKGINFO")
            if not f:
                unmatched.append((os.path.basename(a), "NO-PKGINFO", ""))
                continue
            info = dict(l.split("=", 1)
                        for l in f.read().decode().splitlines() if "=" in l)
    except Exception as e:
        mismatch.append((os.path.basename(a), "UNREADABLE", str(e)[:40]))
        continue
    n = info.get("pkgname", "?").strip()
    v = info.get("pkgver", "?").strip()
    r = info.get("pkgrel", "?").strip()
    tv = tree.get(n)
    resolved = n
    if tv is None and n in ship_map:
        cand = tree.get(ship_map[n])
        if cand is not None and cand[0] == v:
            tv, resolved = cand, ship_map[n]
    if tv is None:
        unmatched.append((os.path.basename(a), n, "%s-%s" % (v, r)))
        continue
    seen.add(resolved)
    if tv[0] != v or tv[1] != r:
        mismatch.append((resolved, "chroot %s-%s" % (v, r),
                         "tree %s-%s" % (tv[0], tv[1])))

for m in sorted(mismatch):
    print(" | ".join(m))
print("MISMATCHES:", len(mismatch))
missing = [n for n in tree if n not in seen]
print("TREE-BUT-NO-ARCHIVE:", len(missing), sorted(missing)[:15])
if unmatched:
    print("UNMATCHED ARCHIVES (no tree recipe resolves — FIX THE MAPPING, "
          "these are DERIVE-INVISIBLE):", len(unmatched))
    for u in sorted(unmatched):
        print("  %-45s pkgname=%-25s %s" % u)
    sys.exit(3)

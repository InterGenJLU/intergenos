#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""tmpfiles.d user/group resolvability gate (fail-closed).

Every /usr/lib/tmpfiles.d/*.conf entry that names a non-root user or
group must be resolvable AT THE OWNING PACKAGE'S OWN INSTALL MOMENT on a
fresh install. The package manager fires each package's canonical hooks
(sysusers first, then tmpfiles) as that package installs — so a tmpfiles
reference to a user provided by a DIFFERENT package is an install-order
race: whichever install order places the referrer first fails its
tmpfiles hook and the package is marked degraded.

Decided 2026-07-19 (ge9b-05 fresh-install finding): swtpm's tmpfiles
fragment referenced user tss, provided only by tpm2-tss's sysusers
fragment — swtpm installed earlier and was marked degraded on an
otherwise-clean 967-package install. The build chroot cannot catch this
by inspecting its own /etc/passwd (users accrete there in build order),
which is why this gate resolves providers STRUCTURALLY, never from the
inspected root's live passwd.

A user/group reference PASSES only if it is provided by:
  (a) a sysusers.d fragment shipped BY THE SAME PACKAGE (per the pkm
      text manifest under <root>/var/lib/igos/packages/), or
  (b) the baseline /etc/passwd–/etc/group shipped by the foundational
      base-files package (always first in install order), read from the
      RECIPE TREE — never from the root's live passwd.

Anything else is a violation and the gate exits 1 (ship-blocking).

Usage:
  audit-tmpfiles-users.py --root /mnt/igos [--packages-dir <repo>/packages]
                          [--quiet]

Runs anywhere a populated root exists (the build chroot at squashfs
time — wired as a build-squashfs.sh audit step — or an installed
system for diagnosis).
"""

import argparse
import re
import sys
from pathlib import Path

# tmpfiles.d entry types that carry user/group fields (systemd-tmpfiles(8)).
_TMPFILES_TYPES = re.compile(r"^[fFdDevqQpLcCbxXrRzZtThHaAm+!$~^=\-]+$")


def parse_tmpfiles_conf(path):
    """Yield (user, group, lineno) for entries naming an owner."""
    for lineno, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4 or not _TMPFILES_TYPES.match(fields[0]):
            continue
        # A leading ':' on mode/user/group is tmpfiles.d(5)'s apply-on-
        # creation-only modifier, not part of the name (systemd 256+;
        # upstream's own provision.conf uses ':root').
        user = fields[3].lstrip(":") if len(fields) >= 4 else "-"
        group = fields[4].lstrip(":") if len(fields) >= 5 else "-"
        yield user or "-", group or "-", lineno


def parse_sysusers_conf(path):
    """Return (users, groups) declared by a sysusers.d fragment.

    'u NAME ...' declares user NAME and (implicitly) group NAME.
    'g NAME ...' declares group NAME.
    """
    users, groups = set(), set()
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        if fields[0] == "u" or fields[0] == "u!":
            users.add(fields[1])
            groups.add(fields[1])
        elif fields[0] == "g":
            groups.add(fields[1])
    return users, groups


def parse_nss_names(path):
    """First-field names from a passwd/group format file."""
    names = set()
    if not path.is_file():
        return names
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and ":" in line:
            names.add(line.split(":", 1)[0])
    return names


def manifest_paths(manifest_file):
    """Normalized absolute file paths listed by a pkm text manifest."""
    paths = set()
    in_hdr = True
    for raw in manifest_file.read_text(errors="replace").splitlines():
        line = raw.rstrip("\n")
        if in_hdr and re.match(r"^[A-Z_ ]+:", line):
            continue
        in_hdr = False
        # strip an optional trailing " sha256:<hex>" suffix
        m = re.search(r"\s+sha256:[0-9a-f]{64}$", line)
        if m:
            line = line[: m.start()]
        line = line.strip().rstrip("/")
        if not line:
            continue
        if not line.startswith("/"):
            line = "/" + line
        paths.add(line)
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, type=Path,
                    help="populated root to audit (build chroot or installed system)")
    ap.add_argument("--packages-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "packages",
                    help="recipe tree root (for the base-files baseline passwd/group)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = args.root
    tmpfiles_dir = root / "usr/lib/tmpfiles.d"
    manifest_dir = root / "var/lib/igos/packages"
    # base-files r13 moved the account databases from files/etc/ to the
    # account-skel reference dir (seeded at Forge's config phase). Prefer
    # the current location; fall back to the pre-r13 path for older trees.
    # (Stale path caught live by this gate's own input check, ge9b-10 mint.)
    base_files = (args.packages_dir /
                  "core/intergenos-base-files/files/usr/share/"
                  "intergenos-base-files/account-skel")
    if not (base_files / "passwd").exists():
        base_files = args.packages_dir / "core/intergenos-base-files/files/etc"

    for req, what in ((tmpfiles_dir, "tmpfiles.d dir"),
                      (manifest_dir, "pkm manifest dir"),
                      (base_files / "passwd", "baseline passwd")):
        if not req.exists():
            print(f"FAIL: required input missing: {what} ({req})")
            return 2

    baseline_users = parse_nss_names(base_files / "passwd")
    baseline_groups = parse_nss_names(base_files / "group")

    # conf path -> owning package name, and package -> its sysusers decls
    owner_of = {}
    pkg_sysusers = {}
    for mf in sorted(manifest_dir.iterdir()):
        if not mf.is_file():
            continue
        paths = manifest_paths(mf)
        pkg = mf.name
        users, groups = set(), set()
        for p in paths:
            if p.startswith("/usr/lib/tmpfiles.d/") and p.endswith(".conf"):
                owner_of[p] = pkg
            if p.startswith("/usr/lib/sysusers.d/") and p.endswith(".conf"):
                frag = root / p.lstrip("/")
                if frag.is_file():
                    u, g = parse_sysusers_conf(frag)
                    users |= u
                    groups |= g
        pkg_sysusers[pkg] = (users, groups)

    violations = []
    checked = 0
    for conf in sorted(tmpfiles_dir.glob("*.conf")):
        conf_abs = "/usr/lib/tmpfiles.d/" + conf.name
        pkg = owner_of.get(conf_abs)
        own_users, own_groups = pkg_sysusers.get(pkg, (set(), set()))
        for user, group, lineno in parse_tmpfiles_conf(conf):
            for name, kind, own, base in (
                (user, "user", own_users, baseline_users),
                (group, "group", own_groups, baseline_groups),
            ):
                if name in ("-", "root") or name.isdigit():
                    continue
                checked += 1
                if name in own or name in base:
                    continue
                origin = pkg if pkg else "UNOWNED (no pkm manifest lists this conf)"
                violations.append(
                    f"{conf.name}:{lineno}: {kind} '{name}' is not provided by the "
                    f"owning package's own sysusers fragment or the base-files "
                    f"baseline (owner: {origin}) — install-order race: the "
                    f"tmpfiles hook fails whenever this package installs before "
                    f"the {kind}'s provider"
                )

    if violations:
        print(f"FAIL: {len(violations)} unresolvable tmpfiles.d owner reference(s):")
        for v in violations:
            print(f"  {v}")
        print("Fix: the owning package ships its own sysusers.d fragment "
              "declaring the user/group (idempotent duplicates are fine — "
              "systemd-sysusers merges agreeing declarations).")
        return 1

    if not args.quiet:
        print(f"OK: {checked} tmpfiles.d owner reference(s) across "
              f"{len(list(tmpfiles_dir.glob('*.conf')))} conf(s) all resolve "
              f"order-independently")
    return 0


if __name__ == "__main__":
    sys.exit(main())

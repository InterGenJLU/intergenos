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

The parser reads each archive name by the versions the recipes under
--packages-dir declare, else by the archive's own .PKGINFO: the same reading
Forge applies on the target, so the audit and the installer cannot disagree
about a name. An archive neither accounts for is reported at ERROR by the
parser and DROPPED here.

Every staged archive's reading is then compared with the name, version and
release its own .PKGINFO states (pkm's own parser, pkm/repo.py). The parser
opens no .PKGINFO for a name a recipe accounts for, and the build's .PKGINFO
sweep (Step 4.7) only checks that the fields are present, so without this an
earlier build whose version ends in what reads as a release (dialog's
1.3-20260107 against a recipe that says 1.3) would install as a build it is
not. The release is read from the .PKGINFO's own text by the rule an archive
name's release is read by (pkm/archive_names.py), not by int(), which takes
'03' or a digit of another script for 3. A disagreement, or a .PKGINFO or a
release in it that cannot be read, fails the audit by name. Each .PKGINFO is
read once here, in full: it is an archive's last member.

Exit codes:
  0 — every staged archive is parsed (and, with --strict-tier, tier-resolvable)
      and agrees with its own .PKGINFO
  1 — one or more archives are dropped / collide / orphaned / disagree with
      their .PKGINFO / carry none that can be read
  2 — argument or environment error
"""

import argparse
import logging
import os
import sys
import tarfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from installer.backend import packages as P
    from pkm.archive_names import _RELEASE_TEXT, parse_archive_filename
    from pkm.repo import _parse_pkginfo
except Exception as e:  # noqa: BLE001 — surface import failure as an env error
    sys.stderr.write(f"ERROR: cannot import installer.backend.packages: {e}\n")
    sys.exit(2)


def _release_text(release):
    return "with no release" if release is None else f"release {release}"


def _pkginfo_text(archive_path):
    """The raw text of an archive's .PKGINFO, or None when it carries none.

    The member is found as pkm/repo.py's _read_package_meta finds it (the first
    regular file named .PKGINFO, decoded as UTF-8); the text is kept so that
    the release can be read from what the header actually says. Raises on a
    read error.
    """
    with tarfile.open(str(archive_path), "r:gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            if os.path.basename(member.name) != ".PKGINFO":
                continue
            handle = tf.extractfile(member)
            if handle is None:
                continue
            return handle.read().decode("utf-8")
    return None


def _pkginfo_release(text):
    """(release, problem) from a .PKGINFO's text.

    One rule for a release, in a name and in a header: the pkgrel line, read
    as written, must be ASCII digits without a leading zero, as
    pkm/archive_names.py reads a release out of an archive name. pkm's own
    parser strips the value and takes it by int(), so ' 3', '03' and a digit
    of another script all read as 3. No pkgrel line at all is (None, None).
    """
    lines = [line for line in text.splitlines()
             if line.partition("=")[0].strip() == "pkgrel"]
    if not lines:
        return None, None
    raw = lines[0][len("pkgrel="):] if lines[0].startswith("pkgrel=") else None
    if len(lines) != 1 or raw is None or not _RELEASE_TEXT.fullmatch(raw):
        return None, ", ".join(ascii(line) for line in lines)
    return int(raw), None


def pkginfo_agreement(files, declared):
    """(agreed, errors) for the staged archive files.

    Each file's name is read the way get_archives reads it: by a recipe's
    declared version when one composes the name, otherwise by its own
    .PKGINFO, in which case the name must compose from that .PKGINFO. The
    name, version and (when the name states one) release so read must be
    the ones the archive's .PKGINFO states.
    """
    agreed, errors = 0, []
    for f in files:
        try:
            text = _pkginfo_text(f)
            meta = _parse_pkginfo(text) if text is not None else None
        except Exception as e:  # noqa: BLE001 — a read or decode error, a malformed field
            errors.append(f"PKGINFO-UNREADABLE: {f.name} -> its .PKGINFO cannot "
                          f"be read ({e})")
            continue
        if not meta or not meta.get("name") or not meta.get("version"):
            errors.append(f"PKGINFO-UNREADABLE: {f.name} -> it carries no .PKGINFO "
                          f"stating its package name and version")
            continue
        release, problem = _pkginfo_release(text)
        if problem:
            errors.append(f"PKGINFO-UNREADABLE: {f.name} -> its .PKGINFO release "
                          f"{problem} is not one release written in ASCII digits "
                          f"without a leading zero, as an archive name writes it")
            continue
        pk = (meta["name"], meta["version"], release)
        named = parse_archive_filename(f.name, declared)
        how = "by its recipe's declared version"
        if named is None or declared.get(named.name) != named.version:
            # No recipe accounts for the name, so its reading is this .PKGINFO.
            named = parse_archive_filename(f.name, {pk[0]: pk[1]})
            how = "by its own .PKGINFO"
            if named is None or (named.name, named.version) != pk[:2]:
                errors.append(
                    f"PKGINFO-DISAGREES: {f.name} -> its .PKGINFO states "
                    f"{pk[0]} {pk[1]} {_release_text(pk[2])}, which does not "
                    f"compose this name")
                continue
        if (named.name, named.version) != pk[:2] or (
                named.release is not None and named.release != pk[2]):
            errors.append(
                f"PKGINFO-DISAGREES: {f.name} reads as {named.name} "
                f"{named.version} {_release_text(named.release)} {how}, but its "
                f".PKGINFO states {pk[0]} {pk[1]} {_release_text(pk[2])} -> it "
                f"would install as a build it is not")
            continue
        agreed += 1
    return agreed, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive-dir", required=True,
                    help="staged archive dir, e.g. /mnt/igos/var/lib/igos/archives")
    ap.add_argument("--packages-dir", default=str(REPO / "packages"),
                    help="packages/ tree whose declared versions read each "
                         "archive name and whose tiers resolve it (default: "
                         "repo packages/)")
    ap.add_argument("--strict-tier", action="store_true",
                    help="also fail if a parsed archive maps to no packages/<tier>/ dir")
    args = ap.parse_args()

    adir = Path(args.archive_dir)
    if not adir.is_dir():
        sys.stderr.write(f"ERROR: archive dir not found: {adir}\n")
        return 2

    pdir = Path(args.packages_dir)
    if not pdir.is_dir():
        sys.stderr.write(f"ERROR: packages dir not found: {pdir}\n")
        return 2

    files = sorted(f for f in adir.iterdir() if f.name.endswith(".igos.tar.gz"))
    if not files:
        sys.stderr.write(f"ERROR: no .igos.tar.gz archives under {adir}\n")
        return 2

    # The parser's own ERROR and WARNING lines (a name it cannot identify, a
    # duplicate) are part of this audit's report.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s: %(message)s")

    # Run the REAL Forge parser — a regression in get_archives() is caught here.
    parsed = P.get_archives(adir, pdir)        # {name: (version, path)}
    parsed_paths = {str(p) for (_v, p) in parsed.values()}

    errors = []

    # 1. Completeness: every staged archive must be represented. A file missing
    #    from parsed_paths either names no package the parser can identify or
    #    was clobbered by a same-name archive — both are the silent-drop class.
    for f in files:
        if str(f) not in parsed_paths:
            errors.append(
                f"DROPPED: {f.name} is staged but Forge get_archives() never "
                f"yields it (no recipe's declared version and no .PKGINFO of "
                f"its own names it, or a duplicate-name clobber) -> "
                f"present in the image, never installed")

    if len(parsed) < len(files):
        errors.append(
            f"COUNT: {len(files)} archives staged but get_archives() returned "
            f"{len(parsed)} unique names (delta={len(files) - len(parsed)} lost)")

    # 2. Tier resolution: a parsed archive that maps to no packages/<tier>/ dir
    #    can never be put in any group's tier set, so get_group_packages would
    #    never select it even though it parses.
    if args.strict_tier:
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

    # 3. Agreement: every staged archive is the build its .PKGINFO says it is.
    declared = P._declared_versions(pdir)
    t0 = time.monotonic()
    agreed, disagreements = pkginfo_agreement(files, declared)
    errors.extend(disagreements)
    print(f"pkginfo agreement: {agreed} of {len(files)} staged archives agree "
          f"with their own .PKGINFO; "
          f"{sum(e.startswith('PKGINFO-DISAGREES') for e in disagreements)} "
          f"disagree; "
          f"{sum(e.startswith('PKGINFO-UNREADABLE') for e in disagreements)} "
          f"unreadable; read in {time.monotonic() - t0:.1f} s")

    if errors:
        sys.stderr.write("install-set audit FAILED:\n")
        for e in errors:
            sys.stderr.write(f"  - {e}\n")
        return 1

    print(f"install-set audit OK: {len(files)} staged archives all parsed by "
          f"Forge get_archives() ({len(parsed)} unique names"
          f"{', all tier-resolvable' if args.strict_tier else ''}"
          f", each the build its own .PKGINFO states).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

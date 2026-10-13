#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Read bash-tier recipe and archive currency without consulting the database.

Drivers read one archive snapshot per invocation, then check the installed
manifest at each package. Nothing is persisted between resumes. The parser
and version comparator are the same ones used by the currency preflight.
"""

import argparse
import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "redeploy_banked_archives", Path(__file__).with_name("redeploy-banked-archives.py"))
_reader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reader)
read_pkginfo = _reader.read_pkginfo
vcompare = _reader.vcompare
load_recipes = _reader.load_recipes


def newest_archives(directory, *, strict=False):
    """Ship name -> (version, numeric release, path), ordered by package key.

Preflight retains its existing treatment of unreadable archives. A driver
requires every archive to be readable before it can certify the newest one;
otherwise an unreadable newer sibling could hide behind a current older one.
"""
    newest = {}
    for archive in sorted(directory.glob("*.igos.tar.gz")):
        info = read_pkginfo(archive)
        if not info or "pkgname" not in info or (strict and (
                not info.get("pkgname") or not info.get("pkgver"))):
            if strict:
                raise ValueError(f"cannot read package identity from {archive}")
            continue
        version = info.get("pkgver", "0")
        release = int(info.get("pkgrel", "1") or 1)
        name = info["pkgname"]
        current = newest.get(name)
        if current is None or vcompare(current[:2], (version, release)) < 0:
            newest[name] = (version, release, archive)
    return newest


def build_states(packages_dir, archives_dir):
    """Yield tab-separated records for the driver's tier, using recipe keys."""
    if not packages_dir.is_dir():
        raise ValueError(f"recipe directory absent: {packages_dir}")
    newest = newest_archives(archives_dir, strict=True)
    recipes = load_recipes(packages_dir.parent)
    for name, recipe in sorted(recipes.items()):
        directory = Path(recipe["path"]).parent
        if directory.parent != packages_dir:
            continue
        version, release = recipe["version"], recipe["release"]
        banked = newest.get(name)
        if banked is None:
            state = "never built"
        elif banked[:2] == (version, release):
            state = f"skipped (current {version}-{release}, archive {banked[2].name})"
        else:
            relation = "behind" if vcompare(banked[:2], (version, release)) < 0 else "differs from"
            state = f"stale: archive {banked[0]}-{banked[1]} {relation} tree {version}-{release}"
        fields = (directory.name, name, version, state)
        if any("\t" in field or "\n" in field or "\r" in field for field in fields):
            raise ValueError(f"invalid currency record for {directory}")
        yield "\t".join(fields)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--packages-dir", type=Path, required=True)
    parser.add_argument("--archives-dir", type=Path, default=Path("/var/lib/igos/archives"))
    args = parser.parse_args()
    try:
        # Finish validation before publishing any records to the driver.
        records = list(build_states(args.packages_dir, args.archives_dir))
    except (OSError, ValueError) as error:
        print(f"bash-tier-build-state: cannot verify currency: {error}", file=sys.stderr)
        return 2
    print("\n".join(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())

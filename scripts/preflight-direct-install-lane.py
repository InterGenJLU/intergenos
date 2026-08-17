#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed preflight gate: direct_install must be declared on a lane that
implements it.

`direct_install: true` is a contract between a recipe and its builder: the
recipe's do_install writes to absolute paths on the live filesystem instead of
into DESTDIR, and the builder is expected to determine what the package owns by
diffing filesystem snapshots taken before and after the build.

Exactly one builder implements the other half of that contract. igos-build takes
the snapshots (igos-build/tracker.py fs_snapshot / diff_new_files) and builds the
manifest and archive from the observed diff. The bash builder does not: its
pipeline is stage -> manifest -> archive -> deploy, the archive is a tar of the
DESTDIR staging tree, and it never reads the flag at all.

Declaring the flag on a package the bash lane builds therefore produces no error
and no warning. The build succeeds, the files land on the live filesystem, the
squashfs picks them up, and the ARCHIVE — the artifact every install is built
from — contains only whatever the license bundler happened to stage. The package
is present on the live image and absent from every installed system.

Measured 2026-07-29 on an installed system built from that corpus: the
pyyaml-pass2 manifest lists five entries, all of them the bundled LICENSE and
its parent directories, and the libyaml C extension the package exists to
deliver is absent from the target with nothing owning its path. The
linux-kernel-pass2 manifest has the identical shape around its COPYING file.
Two for two: this is a property of the lane, not a mistake in one recipe.

The gate is static and cheap — it reads the build drivers and the recipes, never
the chroot — so it runs at preflight and refuses the build before it starts,
rather than surfacing hours later at the squashfs metadata/payload gate or,
worse, on a user's machine after an install.

Exit 0 clean, 1 on violations, 2 on usage/setup errors.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every driver that runs the bash package pipeline. Each invokes packages via a
# `run_package "<recipe-dir>" ...` / `build_*_package "<recipe-dir>" ...` line
# whose first argument is the recipe directory under that driver's tier root.
# Listed explicitly rather than globbed: a new build driver should have to be
# named here deliberately, and a rename must fail loudly instead of quietly
# reducing this gate's coverage to nothing.
BASH_DRIVERS = {
    "scripts/chroot-build-ch8.sh": "packages/core",
    "scripts/chroot-build-ch10.sh": "packages/core",
    "scripts/chroot-build-core-extra.sh": "packages/core",
    "scripts/chroot-build-base.sh": "packages/base",
}

# `run_package "pkg-dir" ...` or `build_base_package "pkg-dir" ...`, first
# argument only, at the start of a line (not inside a comment or a heredoc body).
INVOCATION_RE = re.compile(
    r'^\s*(?:run_package|build_\w+_package)\s+"([^"]+)"')

# Top-level `direct_install: true`, anchored at column 0 so an indented key in a
# nested mapping — which belongs to that mapping, not the package — is not read
# as a package-level declaration.
DIRECT_INSTALL_RE = re.compile(r'^direct_install:\s*true\s*(?:#.*)?$', re.M)


def driver_packages(repo_root: Path) -> dict[str, list[tuple[str, int]]]:
    """Map each bash driver to the (recipe_dir, line_number) pairs it builds."""
    found: dict[str, list[tuple[str, int]]] = {}
    for driver, _tier_root in sorted(BASH_DRIVERS.items()):
        path = repo_root / driver
        if not path.is_file():
            raise FileNotFoundError(
                f"{driver} is missing — this gate's package list would silently "
                f"shrink. Update BASH_DRIVERS if the driver was renamed.")
        entries: list[tuple[str, int]] = []
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            m = INVOCATION_RE.match(line)
            if m:
                entries.append((m.group(1), lineno))
        found[driver] = entries
    return found


def declares_direct_install(recipe: Path) -> bool:
    """True when package.yml declares direct_install at the top level."""
    if not recipe.is_file():
        return False
    return DIRECT_INSTALL_RE.search(recipe.read_text()) is not None


def scan(repo_root: Path) -> list[dict]:
    """Return one violation record per bash-built recipe declaring the flag."""
    violations: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for driver, entries in driver_packages(repo_root).items():
        tier_root = BASH_DRIVERS[driver]
        for pkg_dir, lineno in entries:
            recipe = repo_root / tier_root / pkg_dir / "package.yml"
            if not declares_direct_install(recipe):
                continue
            key = (driver, pkg_dir)
            if key in seen:
                continue
            seen.add(key)
            violations.append({
                "package": pkg_dir,
                "recipe": f"{tier_root}/{pkg_dir}/package.yml",
                "driver": driver,
                "line": lineno,
            })
    return violations


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT),
                    help="repository root to scan (default: this checkout)")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        violations = scan(repo_root)
    except FileNotFoundError as e:
        print(f"[direct-install-lane] SETUP ERROR: {e}", file=sys.stderr)
        return 2

    if not violations:
        print("[direct-install-lane] PASS: no bash-built recipe declares "
              "direct_install")
        return 0

    print(f"[direct-install-lane] HALT: {len(violations)} recipe(s) declare "
          f"direct_install but are built by a lane that does not implement it:",
          file=sys.stderr)
    for v in violations:
        print(f"  {v['package']}", file=sys.stderr)
        print(f"    declared in : {v['recipe']}", file=sys.stderr)
        print(f"    built by    : {v['driver']}:{v['line']}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  The bash builder tars its DESTDIR staging tree into the archive and "
          "never diffs the filesystem, so a do_install that writes to absolute "
          "paths deploys its payload and ships an archive without it. The "
          "package then exists on the live image and is missing from every "
          "install.", file=sys.stderr)
    print("  Resolve either way, but resolve it: stage into DESTDIR and drop the "
          "flag, or move the package to igos-build, which implements the "
          "snapshot diff the flag promises.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

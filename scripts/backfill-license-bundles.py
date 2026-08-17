#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""ONE-TIME backfill of /usr/share/licenses/<pkg>/ for the bash chroot-build-*
tier (K21.B audit closure for 2026-05-24 build cycle).

Why this exists
---------------
K21.B (license-bundle compliance gate, scripts/check-license-bundle.sh) ran
end-to-end for the first time on 2026-05-24 and surfaced a 315-of-791
package gap: every package built via the bash chroot-build-{ch8,base,
core-extra}.sh recipes lacks the /usr/share/licenses/<pkg>/ directory
populated with upstream license text. Root cause: the bundle_license()
hook in igos-build/builder.py (which DOES populate that directory for
Python-builder packages — tier:desktop / extra / ai) was never replicated
into the bash builders.

This script is the unblock-today path. The DURABLE fix is a follow-on
session that adds a bundle_license-equivalent shell function to
scripts/pkg-functions.sh and wires it into the chroot-build-* scripts'
package-install loop. After that recipe-level fix lands, a clean rebuild
produces a chroot with license bundles AT BUILD TIME — and this script
becomes obsolete and can be deleted.

What it does
------------
1. Connects to the chroot's pkm SQLite database to enumerate installed
   packages (name + version pair).
2. For each package, checks whether /usr/share/licenses/<name>/ already
   exists with content. If so, skip — the package's build.sh staged its
   own licenses and we leave it alone (matches builder.py's "don't
   clobber upstream-supplied bundling" rule).
3. Otherwise, locates the package's cached source tarball at
   /mnt/intergenos/build/sources/<name>-<version>.tar.{gz,xz,bz2,zst}
   and extracts it to a temp directory.
4. Walks the extracted tree (top-level + immediate licenses/ /
   LICENSES/ / license-files/ subdirs) for files matching the standard
   upstream license naming pattern (LICENSE / LICENCE / COPYING /
   COPYRIGHT / NOTICE — case-insensitive, with optional .txt / .md /
   suffix variants).
5. Copies matching files into <CHROOT>/usr/share/licenses/<name>/.

Pattern matches igos-build/builder.py:bundle_license() exactly — same
regex, same subdirectory list, same skip-if-already-staged behavior.

Run as root (chroot operations + writes to chroot paths). Reports stats
at the end. Exit 0 if all packages with extractable licenses were
backfilled cleanly. Exit 1 if any package's tarball was missing or
unreadable. Exit 2 on argument / environment error.
"""

import argparse
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


# The four license-bundling strategies (S1-S4) live in the shared module
# igos-build/license_bundle.py — the SINGLE source of truth also imported by
# igos-build/builder.py's build-time hook. This backfill tool delegates to it
# (loaded by file path; the package dir name has a hyphen) so it can never
# drift from the build-time behavior. See that module's docstring for the full
# rationale (the 2026-06-03 duplication-drift fix).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LB_PATH = _REPO_ROOT / "igos-build" / "license_bundle.py"
_lb_spec = importlib.util.spec_from_file_location("igos_license_bundle", _LB_PATH)
license_bundle = importlib.util.module_from_spec(_lb_spec)
_lb_spec.loader.exec_module(license_bundle)

SOURCE_EXTENSIONS = [".tar.xz", ".tar.gz", ".tar.bz2", ".tar.zst", ".tar"]


def assert_root():
    if os.geteuid() != 0:
        sys.stderr.write("ERROR: backfill must run as root (chroot path writes)\n")
        sys.exit(2)


def enumerate_installed(chroot: Path) -> list[tuple[str, str]]:
    """Return [(name, version), ...] from the chroot's pkm.db."""
    db_path = chroot / "var" / "lib" / "igos" / "pkm.db"
    if not db_path.is_file():
        sys.stderr.write(f"ERROR: pkm.db not found at {db_path}\n")
        sys.exit(2)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.execute("SELECT name, version FROM installed ORDER BY name;")
        return [(row[0], row[1]) for row in cur.fetchall()]
    finally:
        con.close()


def find_source_tarball(
    sources_dir: Path,
    name: str,
    version: str,
    package_yml: Path | None = None,
) -> Path | None:
    """Locate the cached source tarball for <name>-<version>.

    Strategy:
    1. Try <name>-<version>.<ext> for each known extension (covers ~95% of
       packages where pkm name == tarball name).
    2. If package_yml is provided, parse its `source:` field and derive
       the tarball basename from the URL (covers cases like pkm name
       'ntfs' with tarball 'ntfs-3g_ntfsprogs-<version>.tgz').

    Returns None if neither finds a match.
    """
    base = f"{name}-{version}"
    for ext in SOURCE_EXTENSIONS + [".tgz", ".tbz2", ".txz"]:
        candidate = sources_dir / f"{base}{ext}"
        if candidate.is_file():
            return candidate

    if package_yml is not None and package_yml.is_file():
        try:
            import yaml
            with package_yml.open() as f:
                data = yaml.safe_load(f) or {}
            sources = data.get("source") or []
            if isinstance(sources, list) and sources:
                first = sources[0]
                if isinstance(first, dict):
                    url = first.get("url", "")
                    # Substitute ${version} from the package.yml's version,
                    # not from the pkm record (in case pkm normalized it).
                    pkg_version = data.get("version", version)
                    url = url.replace("${version}", str(pkg_version))
                    basename = url.rsplit("/", 1)[-1]
                    candidate = sources_dir / basename
                    if candidate.is_file():
                        return candidate
        except (OSError, ImportError, Exception):
            pass

    return None


def extract_tarball(tarball: Path, dest: Path) -> bool:
    """Extract `tarball` into `dest`. Returns True on success.

    Uses the system `tar` binary for all formats (not Python's tarfile)
    because Python 3.12+'s `filter='data'` rejects tarballs containing
    absolute symlinks (e.g., libtirpc-1.3.7's INSTALL → /usr/share/automake/INSTALL
    autoconf convention). `tar` handles these safely by default. Auto-
    detection (-a / -xf) picks the right decompressor.
    """
    try:
        subprocess.run(
            ["tar", "-xaf", str(tarball), "-C", str(dest)],
            check=True,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError) as e:
        sys.stderr.write(f"  WARN: extract failed for {tarball.name}: {e}\n")
        return False


def backfill_one(
    name: str,
    version: str,
    chroot: Path,
    sources_dir: Path,
    firstparty_license: Path | None,
    package_yml_map: dict[str, Path],
) -> str:
    """Backfill one package's license bundle.

    Returns one of:
        'skipped'      — already bundled
        'backfilled'   — newly bundled from upstream tarball
        'firstparty'   — bundled from project-level GPL-3.0 (first-party pkg)
        'pass-mirror'  — bundled by mirroring base package's licenses
        'spdx-stub'    — bundled with a minimal LICENSE-by-SPDX stub
        'no-tarball'   — no tarball + no fallback strategy matched
        'no-licenses'  — tarball found but no license files inside + no SPDX
        'error'        — extraction failed
    """
    license_dir = chroot / "usr" / "share" / "licenses" / name
    if license_bundle.already_bundled(license_dir):
        return "skipped"

    # S1 source: extract the cached upstream tarball (if any) to a temp dir so
    # the shared strategy module can scan it. The build-time hook receives an
    # already-extracted tree; this tool reconstructs one from the tarball.
    src_root = None
    tmp_ctx = None
    tarball = find_source_tarball(
        sources_dir, name, version,
        package_yml=package_yml_map.get(name),
    )
    if tarball is not None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="license-extract-")
        tmp_path = Path(tmp_ctx.name)
        if not extract_tarball(tarball, tmp_path):
            tmp_ctx.cleanup()
            return "error"
        src_root = license_bundle.find_source_root(tmp_path)

    # S2 base: a pass-variant mirrors the base package's already-installed
    # license dir (in the chroot's live install root).
    base_name = license_bundle.base_name_for_pass_variant(name)
    base_license_dir = (
        chroot / "usr" / "share" / "licenses" / base_name if base_name else None
    )

    # S4 metadata: SPDX identifier + tier from package.yml.
    spdx = None
    tier = "unknown"
    yml_path = package_yml_map.get(name)
    if yml_path is not None and yml_path.is_file():
        try:
            import yaml  # local import (PyYAML may be absent on a minimal host)
            with yml_path.open() as f:
                pkg_data = yaml.safe_load(f) or {}
            spdx = pkg_data.get("license") or pkg_data.get("payload_license")
            tier = pkg_data.get("tier", "unknown")
        except Exception:
            pass

    try:
        result = license_bundle.apply_strategies(
            name, version, license_dir,
            src_root=src_root,
            base_license_dir=base_license_dir,
            firstparty_license=firstparty_license,
            spdx=spdx,
            tier=tier,
        )
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    # Preserve backfill's two legacy reporting buckets: distinguish "had a
    # tarball but no extractable text + no fallback" from "no tarball at all".
    if result == "no-licenses":
        return "no-licenses" if tarball is not None else "no-tarball"
    return result


def build_package_yml_map(packages_root: Path) -> dict[str, Path]:
    """Map every package name to its package.yml path."""
    result: dict[str, Path] = {}
    for yml in packages_root.rglob("package.yml"):
        try:
            import yaml
            with yml.open() as f:
                data = yaml.safe_load(f) or {}
            name = data.get("name")
            if name:
                result[name] = yml
        except (OSError, Exception):
            continue
    return result


def main():
    tree_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--chroot",
        default="/mnt/igos",
        help="Chroot root (default: /mnt/igos)",
    )
    ap.add_argument(
        "--sources-dir",
        default=str(tree_root / "build" / "sources"),
        help="Cached source tarball directory",
    )
    ap.add_argument(
        "--firstparty-license",
        default=str(tree_root / "LICENSE"),
        help="Path to project-level LICENSE file (used for first-party "
             "InterGenOS packages with no upstream tarball)",
    )
    ap.add_argument(
        "--packages-root",
        default=str(tree_root / "packages"),
        help="Root of packages/ tree (for SPDX-stub fallback strategy)",
    )
    args = ap.parse_args()

    assert_root()

    chroot = Path(args.chroot)
    sources_dir = Path(args.sources_dir)

    if not chroot.is_dir():
        sys.stderr.write(f"ERROR: chroot {chroot} not a directory\n")
        sys.exit(2)
    if not sources_dir.is_dir():
        sys.stderr.write(f"ERROR: sources-dir {sources_dir} not a directory\n")
        sys.exit(2)

    firstparty_license = Path(args.firstparty_license)
    if not firstparty_license.is_file():
        sys.stderr.write(
            f"WARN: --firstparty-license {firstparty_license} not found; "
            f"first-party strategy disabled\n"
        )
        firstparty_license = None  # type: ignore

    packages_root = Path(args.packages_root)
    package_yml_map = build_package_yml_map(packages_root) if packages_root.is_dir() else {}

    packages = enumerate_installed(chroot)
    print(f"Backfilling license bundles for {len(packages)} installed packages...")
    print(f"  Chroot:              {chroot}")
    print(f"  Sources dir:         {sources_dir}")
    print(f"  First-party LICENSE: {firstparty_license}")
    print(f"  Packages mapped:     {len(package_yml_map)}")
    print()

    counts: dict[str, int] = {
        "skipped": 0,
        "backfilled": 0,
        "firstparty": 0,
        "pass-mirror": 0,
        "spdx-stub": 0,
        "no-tarball": 0,
        "no-licenses": 0,
        "error": 0,
    }
    no_tarball_list: list[str] = []
    no_licenses_list: list[str] = []
    error_list: list[str] = []

    for name, version in packages:
        result = backfill_one(
            name, version, chroot, sources_dir,
            firstparty_license, package_yml_map,
        )
        counts[result] += 1
        if result == "backfilled":
            print(f"  [OK-TARBALL]    {name}-{version}")
        elif result == "firstparty":
            print(f"  [OK-FIRSTPARTY] {name}-{version}")
        elif result == "pass-mirror":
            print(f"  [OK-PASSMIRROR] {name}-{version}")
        elif result == "spdx-stub":
            print(f"  [OK-SPDXSTUB]   {name}-{version}")
        elif result == "no-tarball":
            no_tarball_list.append(f"{name}-{version}")
        elif result == "no-licenses":
            no_licenses_list.append(f"{name}-{version}")
        elif result == "error":
            error_list.append(f"{name}-{version}")

    print()
    print("=== Backfill Summary ===")
    print(f"  Total packages:          {len(packages)}")
    print(f"  Already bundled:         {counts['skipped']}")
    print(f"  Newly bundled (tarball): {counts['backfilled']}")
    print(f"  Newly bundled (firstparty GPL-3.0): {counts['firstparty']}")
    print(f"  Newly bundled (pass-variant mirror): {counts['pass-mirror']}")
    print(f"  Newly bundled (SPDX-stub): {counts['spdx-stub']}")
    print(f"  No source tarball + no fallback: {counts['no-tarball']}")
    print(f"  Tarball had no license + no fallback: {counts['no-licenses']}")
    print(f"  Extract errors:          {counts['error']}")

    if no_tarball_list:
        print()
        print(f"Packages without a cached source tarball at {sources_dir}:")
        for entry in no_tarball_list:
            print(f"  - {entry}")
        print("  (likely first-party InterGenOS packages or source-less / bind-mounted source)")

    if no_licenses_list:
        print()
        print("Packages whose upstream tarball had no LICENSE/COPYING/COPYRIGHT/NOTICE file:")
        for entry in no_licenses_list:
            print(f"  - {entry}")
        print("  (may need a hand-authored LICENSE-by-SPDX file in the package's assets/)")

    if error_list:
        print()
        print("Packages with extraction errors (manual review needed):")
        for entry in error_list:
            print(f"  - {entry}")
        sys.exit(1)

    print()
    print("Done. Re-run scripts/check-license-bundle.sh to verify K21.B compliance.")


if __name__ == "__main__":
    main()

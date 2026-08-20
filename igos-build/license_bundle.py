# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Shared per-package license-bundling strategies (S1-S4).

**Single source of truth** for populating ``/usr/share/licenses/<pkg>/``.
Consumed by:

  - ``igos-build/builder.py:bundle_license`` — the build-time hook for the
    Python tiers (desktop / ai / extra). Runs on the HOST against an
    already-extracted source tree, writing into ``DESTDIR``.
  - ``scripts/backfill-license-bundles.py`` — the one-time / diagnostic
    backfill tool. Extracts a cached tarball, then delegates here.

The bash tiers (core / base via ``scripts/pkg-functions.sh:bundle_license``)
implement the SAME four strategies in **pure bash** — they run *inside the
chroot*, where ``python`` itself may not yet exist (it is built mid-``ch8``),
so they cannot import this module. The two runtimes are kept in lockstep by
shared semantics + the K21.B gate (``scripts/check-license-bundle.sh`` at
``phase_squashfs``), which fails the build loudly if EITHER path leaves a
package without a bundle.

Why this module exists
----------------------
Until 2026-06-03 the four strategies lived only in
``backfill-license-bundles.py`` (a post-hoc tool), while the *build-time*
hooks (``builder.py`` + ``pkg-functions.sh``) implemented only **strategy 1**.
A clean build therefore left ~71 packages without a bundle and tripped K21.B
at ``phase_squashfs``; the 2026-06-02 ISO only got past it via an in-chroot
backfill (a patch, not a recipe fix). Hoisting S1-S4 into this one module and
having both ``builder.py`` and ``backfill-license-bundles.py`` import it kills
the duplication-drift that caused the gap (S1-in-two-places, S2/3/4-in-neither).

Strategies, applied in order — first to populate the dir wins:

  S1 upstream-extract : LICENSE / COPYING / COPYRIGHT / NOTICE in the source
                        tree — top-level + ``licenses`` / ``license-files`` /
                        ``licence-files`` / ``doc`` / ``docs`` subdirs + a
                        single-nested-root recurse.
  S2 pass-variant     : ``*-pass1`` / ``-pass2`` / ``-pam`` / ``-static`` whose
                        own source carried no license → mirror the BASE
                        package's already-installed license dir.
  S3 first-party      : ``intergen-`` / ``intergenos-`` / ``pkm`` /
                        ``igos-build`` / ``forge`` / ``*-helper`` → ship the
                        project GPL-3.0-or-later LICENSE.
  S4 spdx-stub        : a declared SPDX license but no extractable text →
                        write a ``LICENSE-BY-SPDX`` attribution stub (the SPDX
                        identifier is the legally-binding declaration; the full
                        text just isn't bundled).
"""

import re
import shutil
from pathlib import Path

# Matches: LICENSE, LICENSE.txt, LICENSE.md, LICENSE-MIT, LICENSE.APACHE2,
# LICENCE (British), COPYING, COPYING.LIB, COPYING.LESSER, COPYRIGHT,
# COPYRIGHT.txt, NOTICE, NOTICE.txt (Apache projects).
LICENSE_PATTERN = re.compile(
    r"^(LICENSE|LICENCE|COPYING|COPYRIGHT|NOTICE)([-_.][\w.-]*)?$",
    re.IGNORECASE,
)

# Subdirs (case-insensitive) scanned in addition to the source tree top level.
LICENSE_SUBDIRS = {"licenses", "license-files", "licence-files", "doc", "docs"}

# First-party InterGenOS package prefixes — get the project-level GPL-3.0-or-later
# LICENSE when they ship no upstream license file (S3).
FIRSTPARTY_PREFIXES = (
    "intergen-",
    "intergenos-",
    "pkm",
    "igos-build",
    "forge",
)

# Suffix patterns marking a "pass-N variant" of a base package (LFS Ch5/6/7
# convention + the FDE *-static binaries). E.g. shadow-pam shares with shadow;
# systemd-pass2 shares with systemd; cryptsetup-static shares with cryptsetup.
PASS_VARIANT_SUFFIXES = (
    "-pass1",
    "-pass2",
    "-pam",
    "-static",
)

# Helper packages that wrap proprietary downloads — first-party GPL-3.0-or-later.
HELPER_SUFFIX = "-helper"


def _scan_dir_for_licenses(directory, rel_prefix=""):
    """Scan one directory level for files matching LICENSE_PATTERN.

    Returns [(absolute_path, relative_dest_name), ...].
    """
    found = []
    try:
        for entry in directory.iterdir():
            if entry.is_file() and LICENSE_PATTERN.match(entry.name):
                rel = f"{rel_prefix}{entry.name}" if rel_prefix else entry.name
                found.append((entry, rel))
    except OSError:
        pass
    return found


def find_source_root(extracted):
    """Return the source-tree root inside ``extracted``.

    Most tarballs have exactly one top-level directory (e.g. ``bash-5.2.21/``).
    If the tarball extracts flat or has multiple top-level entries, return
    ``extracted`` as-is. Used by callers that extract a tarball to a temp dir
    (backfill); the build-time hook already receives the stripped source root.
    """
    extracted = Path(extracted)
    try:
        entries = list(extracted.iterdir())
    except OSError:
        return extracted
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extracted


def find_license_files(src_root):
    """Walk top-level + license-named subdirs + nested project root.

    Returns [(absolute_path, relative_dest_name), ...].

    Search order:
      1. Top level of ``src_root`` (most common: LICENSE, COPYING, ...).
      2. Known license-name subdirs: licenses/, license-files/, doc/, docs/.
      3. Nested project root: if ``src_root`` has exactly one subdirectory and
         no license files at the top level, recurse into that subdir (covers
         tarballs with a nested layout like ``nspr-4.38.2/nspr/LICENSE``).
    """
    src_root = Path(src_root)
    found = []

    # Pass 1: top level.
    found.extend(_scan_dir_for_licenses(src_root))

    # Pass 2: known license-named subdirs.
    try:
        for entry in src_root.iterdir():
            if entry.is_dir() and entry.name.lower() in LICENSE_SUBDIRS:
                found.extend(_scan_dir_for_licenses(entry, rel_prefix=f"{entry.name}/"))
    except OSError:
        pass

    if found:
        return found

    # Pass 3: nested project root (single-subdir case, no top-level license).
    try:
        subdirs = [e for e in src_root.iterdir() if e.is_dir()]
        if len(subdirs) == 1:
            nested = subdirs[0]
            found.extend(_scan_dir_for_licenses(nested))
            for entry in nested.iterdir():
                if entry.is_dir() and entry.name.lower() in LICENSE_SUBDIRS:
                    found.extend(
                        _scan_dir_for_licenses(entry, rel_prefix=f"{entry.name}/")
                    )
    except OSError:
        pass

    return found


def already_bundled(license_dir):
    """True if ``license_dir`` exists and holds at least one regular file."""
    license_dir = Path(license_dir)
    if not license_dir.is_dir():
        return False
    for child in license_dir.rglob("*"):
        if child.is_file():
            return True
    return False


def _newest_ctime(license_dir):
    """Newest inode-change time among the regular files under ``license_dir``.

    ctime, not mtime: the filesystem-diff tracking for direct_install
    packages keys on created-or-ctime-modified (builder.diff_new_files), and
    mtime survives copy2 from an old source tree — a bundle written THIS
    build can carry an old mtime but never an old ctime.
    """
    newest = 0.0
    for child in Path(license_dir).rglob("*"):
        try:
            if child.is_file():
                newest = max(newest, child.stat().st_ctime)
        except OSError:
            continue
    return newest


def is_firstparty(name):
    """True if ``name`` matches a first-party InterGenOS pattern (S3)."""
    if name.endswith(HELPER_SUFFIX):
        return True
    return any(name.startswith(p) or name == p for p in FIRSTPARTY_PREFIXES)


def base_name_for_pass_variant(name):
    """Strip a PASS-variant suffix → base package name. None if no match (S2)."""
    for suffix in PASS_VARIANT_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def spdx_stub_text(name, version, spdx, tier="unknown"):
    """The exact LICENSE-BY-SPDX stub body (S4).

    Kept identical to the bash hook's stub (scripts/pkg-functions.sh) so the
    two builder runtimes emit byte-equivalent attribution.
    """
    return (
        f"{name} {version}\n"
        f"\n"
        f"This package is licensed under: {spdx}\n"
        f"\n"
        f"SPDX identifier is the canonical license-of-record. The\n"
        f"upstream source tree did not include an explicit\n"
        f"LICENSE/COPYING/COPYRIGHT/NOTICE file at a standard path,\n"
        f"so this stub serves as the per-package license attribution\n"
        f"per InterGenOS K21.B compliance gate.\n"
        f"\n"
        f"For the full license text, refer to the canonical SPDX\n"
        f"reference at https://spdx.org/licenses/{spdx}.html (or the\n"
        f"applicable subentry for compound SPDX expressions).\n"
        f"\n"
        f"Source-of-record: packages/{tier}/{name}/package.yml\n"
    )


def apply_strategies(
    name,
    version,
    license_dir,
    *,
    src_root=None,
    base_license_dir=None,
    firstparty_license=None,
    spdx=None,
    tier="unknown",
    stale_before=None,
):
    """Populate ``license_dir`` for one package, trying S1-S4 in order.

    Args:
        name, version: package identity (stub attribution).
        license_dir: destination ``.../usr/share/licenses/<name>/`` (Path).
        src_root: already-extracted source tree to scan for S1 (None to skip).
        base_license_dir: the base package's installed license dir for S2
            (None to skip; the caller derives it from
            ``base_name_for_pass_variant`` against the live install root).
        firstparty_license: path to the project LICENSE for S3 (None to skip).
        spdx: SPDX identifier for the S4 stub (None/empty to skip).
        tier: package tier, for the stub's source-of-record line.
        stale_before: timestamp (time.time() epoch) separating this build's
            writes from prior-build residue, or None to treat any existing
            content as authoritative. Callers whose install root persists
            across builds (the direct_install live root "/") MUST pass the
            build's start time: on such a root, content whose every file
            predates the build is a PRIOR build's bundle, and honoring it as
            "already staged" leaves the re-bundle out of this build's
            filesystem diff — the fresh archive then carries no license paths
            while the on-disk dir becomes archive-unowned (the union gate
            removes it, and K21.B fails at the next squashfs). Measured on the
            2026-08-20 dbus-pass2/systemd-pass2 rebuilds. DESTDIR-staged
            callers keep None — a staging tree is born empty, so any content
            there is this build's by construction.

    Returns one of: ``skipped`` | ``backfilled`` | ``pass-mirror`` |
    ``firstparty`` | ``spdx-stub`` | ``no-licenses``. Never raises on a
    missing-license condition — that is a warning, caught by the K21.B gate.
    """
    license_dir = Path(license_dir)
    if already_bundled(license_dir):
        if stale_before is None or _newest_ctime(license_dir) >= stale_before:
            return "skipped"
        # Every file predates this build: prior-build residue, not this
        # build's staging. Clear it and re-apply the strategies so the fresh
        # bundle enters this build's filesystem diff (and thus its archive).
        shutil.rmtree(license_dir, ignore_errors=True)

    # --- S1: upstream license files in the source tree -------------------
    if src_root is not None:
        files = find_license_files(src_root)
        if files:
            license_dir.mkdir(parents=True, exist_ok=True)
            for src_path, rel_dest in files:
                dest = license_dir / rel_dest
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src_path, dest)
                except OSError:
                    continue
            if already_bundled(license_dir):
                return "backfilled"

    # --- S2: pass-variant — mirror the base package's installed licenses --
    if base_license_dir is not None and already_bundled(base_license_dir):
        base_license_dir = Path(base_license_dir)
        license_dir.mkdir(parents=True, exist_ok=True)
        for entry in base_license_dir.iterdir():
            try:
                if entry.is_file():
                    shutil.copy2(entry, license_dir / entry.name)
                elif entry.is_dir():
                    shutil.copytree(entry, license_dir / entry.name, dirs_exist_ok=True)
            except OSError:
                continue
        if already_bundled(license_dir):
            return "pass-mirror"

    # --- S3: first-party InterGenOS package — ship the project GPL-3.0 ----
    if (
        is_firstparty(name)
        and firstparty_license is not None
        and Path(firstparty_license).is_file()
    ):
        license_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(firstparty_license, license_dir / "LICENSE")
            return "firstparty"
        except OSError:
            pass

    # --- S4: SPDX-only stub ----------------------------------------------
    if spdx:
        license_dir.mkdir(parents=True, exist_ok=True)
        (license_dir / "LICENSE-BY-SPDX").write_text(
            spdx_stub_text(name, version, spdx, tier)
        )
        return "spdx-stub"

    return "no-licenses"

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Archive-manifest coverage: which archives a manifest promises, which the
media carries, and the ISO manifest as the shipped subset of the full one.

Two manifests exist from one build:

  intergenos-archive-manifest.txt      the FULL census — every archive the
                                       build chroot holds; the mirror's
                                       manifest (publish-repo.sh ships it).
  intergenos-archive-manifest-iso.txt  the full census MINUS the mirror-only
                                       archives build-squashfs keeps off the
                                       ISO (derive-iso-exclusions
                                       --mode=archive-excludes). This is the
                                       one the ISO carries at
                                       /install/intergenos-archive-manifest.txt.

Origin: the R001.2 install aborted at the installer's integrity check because
the ISO carried the full manifest (1,146 entries) over a media that ships 862
archives by design. The staging gate had only ever asked "is every staged
archive in the manifest?"; this module supplies the derivation and BOTH
coverage directions so the gate can also ask "is every manifest entry
staged?" and refuse at build time, not at the install.

Used by scripts/derive-iso-archive-manifest.py (the derivation) and
scripts/check-install-integrity-staging.sh (the gate). No dependency on the
installer package: the gate runs on the build host against the tree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

#: Exclusion lines from derive-iso-exclusions --mode=archive-excludes are
#: chroot-relative (``var/lib/igos/archives/<name>-<ver>.igos.tar.gz``);
#: manifest entries are relative to the archive dir. Strip this to compare.
ARCHIVES_PREFIX = "var/lib/igos/archives/"

MANIFEST_VERSION_LINE = "# Manifest-version: 1"
TERMINATOR_LINE = "# End of manifest."
SCOPE_PREFIX = "# Manifest-scope:"
EXCLUDED_PREFIX = "# Archives-excluded:"

_SHA256_LINE = re.compile(
    r"^SHA256 \((?P<path>[^)]+)\) = (?P<sha>[0-9a-fA-F]{64})$")


def normalize_archive_path(path: str) -> str:
    """One name for one archive: archive-dir-relative, forward slashes."""
    p = path.strip().replace("\\", "/")
    if p.startswith(ARCHIVES_PREFIX):
        p = p[len(ARCHIVES_PREFIX):]
    return p


def read_excludes(path: Path) -> set[str]:
    """Archive names (archive-dir-relative) from an exclusion file.

    Blank lines and ``#`` comments are ignored; the chroot-relative prefix
    is stripped so the names compare directly with manifest entries.
    """
    out: set[str] = set()
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.add(normalize_archive_path(line))
    return out


@dataclass
class DeriveResult:
    text: str
    kept: int
    dropped: list[str] = field(default_factory=list)
    excludes_absent: list[str] = field(default_factory=list)


def derive_iso_manifest(full_text: str, excludes: Iterable[str]) -> DeriveResult:
    """The ISO manifest: the full manifest's lines minus the excluded archives.

    Every comment line of the full manifest is kept (build id, timestamps,
    trace pointers); ``# Manifest-scope: iso`` and ``# Archives-excluded: N``
    are placed directly after the version line, replacing any scope line the
    input already carries. Entry order is preserved. Refuses (ValueError) a
    manifest without the version header or the terminator, a malformed line,
    and a derivation that would leave zero entries.
    """
    excl = {normalize_archive_path(e) for e in excludes}
    lines = full_text.splitlines()
    if MANIFEST_VERSION_LINE not in lines:
        raise ValueError(f"full manifest lacks the {MANIFEST_VERSION_LINE!r} header")
    if not lines or lines[-1] != TERMINATOR_LINE:
        raise ValueError(f"full manifest does not end with {TERMINATOR_LINE!r}")

    out: list[str] = []
    kept = 0
    dropped: list[str] = []
    seen: set[str] = set()
    for lineno, line in enumerate(lines, start=1):
        if line.startswith(SCOPE_PREFIX) or line.startswith(EXCLUDED_PREFIX):
            continue  # rewritten below
        if not line or line.startswith("#"):
            out.append(line)
            if line == MANIFEST_VERSION_LINE:
                out.append(f"{SCOPE_PREFIX} iso")
                out.append("__EXCLUDED_PLACEHOLDER__")
            continue
        m = _SHA256_LINE.match(line)
        if not m:
            raise ValueError(f"full manifest line {lineno}: malformed: {line!r}")
        name = normalize_archive_path(m.group("path"))
        seen.add(name)
        if name in excl:
            dropped.append(name)
            continue
        out.append(line)
        kept += 1

    if kept == 0:
        raise ValueError("derivation left zero entries — refusing to write an "
                         "empty ISO manifest")
    excludes_absent = sorted(excl - seen)
    text = "\n".join(out).replace(
        "__EXCLUDED_PLACEHOLDER__", f"{EXCLUDED_PREFIX} {len(dropped)}") + "\n"
    return DeriveResult(text=text, kept=kept, dropped=sorted(dropped),
                        excludes_absent=excludes_absent)


def shipped_set(archive_dir: Path, excludes: Iterable[str]) -> set[str]:
    """The archives the media will carry: every ``*.igos.tar.gz`` under the
    archive dir (archive-dir-relative, posix) minus the exclusion set."""
    base = Path(archive_dir)
    excl = {normalize_archive_path(e) for e in excludes}
    found = {
        p.relative_to(base).as_posix()
        for p in base.rglob("*.igos.tar.gz") if p.is_file()
    }
    return found - excl


def coverage(manifest_entries: Iterable[str],
             shipped: Iterable[str]) -> tuple[list[str], list[str]]:
    """Both directions, sorted:

    unmanifested — shipped archives the manifest does not list (would ship
                   unverified; closes red-team R3);
    missing      — manifest entries with no shipped archive (the media
                   promises what it does not carry; the R001.2 abort).
    """
    entries = {normalize_archive_path(e) for e in manifest_entries}
    ship = {normalize_archive_path(s) for s in shipped}
    return sorted(ship - entries), sorted(entries - ship)

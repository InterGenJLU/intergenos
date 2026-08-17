# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Regression test for PKM-A02 — install captures `release` from .PKGINFO.

The phantom-23 root cause: installer.install() omitted release= from the
add_installed() call, so every install recorded release=1 regardless of the
archive's real pkgrel. Once the mirror index started carrying release
(GBC003.3), that mismatch surfaced as a flood of phantom 'release-only'
upgrades. This gate builds a real archive with a known pkgrel, installs it, and
asserts the DB recorded that release.
"""
import tarfile
from pathlib import Path

import pytest

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


def _build_archive(tmp, name, version, pkgrel):
    """Minimal .igos.tar.gz with a root .PKGINFO (carrying pkgrel) + one file."""
    staging = Path(tmp) / f"build-{name}"
    (staging / "usr").mkdir(parents=True, exist_ok=True)
    (staging / "usr" / f"{name}.txt").write_text("payload\n")
    lines = [f"pkgname={name}", f"pkgver={version}"]
    if pkgrel is not None:
        lines.append(f"pkgrel={pkgrel}")
    lines += ["pkgdesc=test pkg", "license=GPL", "tier=core",
              "builddate=2026-06-16T00:00:00Z", "size=8", "filecount=1"]
    (staging / ".PKGINFO").write_text("\n".join(lines) + "\n")
    archive = Path(tmp) / f"{name}-{version}.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(staging / ".PKGINFO", arcname=".PKGINFO")
        tf.add(staging / "usr" / f"{name}.txt", arcname=f"usr/{name}.txt")
    return str(archive)


def test_install_captures_release_from_pkginfo(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db = PackageDB(tmp_path / "test.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        archive = _build_archive(tmp_path, "rel-pkg", "1.0.0", 5)
        ok, msg = inst.install("rel-pkg", archive_path=archive,
                               install_reason="manual")
        assert ok, f"install failed: {msg}"
        row = db.get_installed("rel-pkg")
        assert row is not None, "package not registered"
        assert row["release"] == 5, f"PKM-A02: expected release 5, got {row['release']}"
    finally:
        db.close()


def test_install_release_defaults_to_one_when_pkgrel_absent(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db = PackageDB(tmp_path / "test.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        archive = _build_archive(tmp_path, "norel-pkg", "2.0", None)
        ok, msg = inst.install("norel-pkg", archive_path=archive,
                               install_reason="manual")
        assert ok, f"install failed: {msg}"
        row = db.get_installed("norel-pkg")
        assert row["release"] == 1
    finally:
        db.close()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for scripts/check-iso-metadata-sync.py (build-squashfs Step 2.7).

The gate makes the metadata/payload split unshippable: a pkm.db row or text
manifest describing a different (version, release) than the shipping
archive, a claimed path absent from the image, or image content matching no
claiming archive each fail the build with the package named. Bootstrap
twins sharing byte-identical paths and config-phase-rewritten etc/ files
must NOT fail — those are the two legitimate divergence classes.
"""

import importlib.util
import io
import os
import sqlite3
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "iso_metadata_sync_test",
        REPO_ROOT / "scripts" / "check-iso-metadata-sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_archive(archives_dir, name, version, release, files):
    """Build <name>-<version>.igos.tar.gz with ./.PKGINFO + the given files.

    files: {path: bytes} — parent dirs are added automatically.
    """
    pkginfo = (
        f"pkgname={name}\npkgver={version}\npkgrel={release}\n"
    ).encode()
    archive = archives_dir / f"{name}-{version}.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(pkginfo)
        tf.addfile(info, io.BytesIO(pkginfo))
        dirs_added = set()
        for path, content in files.items():
            parts = Path(path).parts
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                if d not in dirs_added:
                    dinfo = tarfile.TarInfo(f"./{d}")
                    dinfo.type = tarfile.DIRTYPE
                    tf.addfile(dinfo)
                    dirs_added.add(d)
            finfo = tarfile.TarInfo(f"./{path}")
            finfo.size = len(content)
            tf.addfile(finfo, io.BytesIO(content))
    return archive


class TestIsoMetadataSync(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.chroot = Path(self._tmp.name)
        self.archives = self.chroot / "var/lib/igos/archives"
        self.manifests = self.chroot / "var/lib/igos/packages"
        self.archives.mkdir(parents=True)
        self.manifests.mkdir(parents=True)
        self.db_path = self.chroot / "var/lib/igos/pkm.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE installed (name TEXT, version TEXT, "
            "release INTEGER, superseded_by TEXT)")
        conn.commit()
        conn.close()
        self.mod = _load()

    def tearDown(self):
        self._tmp.cleanup()

    # -- fixture helpers ---------------------------------------------------

    def _register(self, name, version, release):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO installed VALUES (?, ?, ?, NULL)",
            (name, version, release))
        conn.commit()
        conn.close()

    def _manifest(self, name, version, release=None):
        lines = [f"PACKAGE NAME: {name}-{version}",
                 f"PACKAGE VERSION: {version}"]
        if release is not None:
            lines.append(f"PACKAGE RELEASE: {release}")
        lines += ["FILE LIST:", ""]
        (self.manifests / f"{name}-{version}").write_text("\n".join(lines))

    def _deploy(self, files):
        for path, content in files.items():
            dest = self.chroot / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

    def _run(self, extra_args=()):
        argv = ["check-iso-metadata-sync", "--chroot", str(self.chroot),
                "--progress-every", "0", *extra_args]
        out = io.StringIO()
        old_argv = os.sys.argv
        os.sys.argv = argv
        try:
            with redirect_stdout(out):
                rc = self.mod.main()
        finally:
            os.sys.argv = old_argv
        return rc, out.getvalue()

    # -- cases -------------------------------------------------------------

    def test_consistent_package_passes(self):
        files = {"usr/bin/alpha": b"alpha payload"}
        _make_archive(self.archives, "alpha", "1.0", "3", files)
        self._register("alpha", "1.0", 3)
        self._manifest("alpha", "1.0", release=3)
        self._deploy(files)
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)

    def test_release_split_fails_named(self):
        files = {"usr/bin/alpha": b"alpha payload"}
        _make_archive(self.archives, "alpha", "1.0", "10", files)
        self._register("alpha", "1.0", 1)          # the 198-class row
        self._manifest("alpha", "1.0")
        self._deploy(files)
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("metadata split", out)
        self.assertIn("1.0-r1", out)
        self.assertIn("1.0-r10", out)
        self.assertIn("remedy: ", out)

    def test_sealed_hook_members_are_archive_metadata_not_payload(self):
        """`.scripts/` members (the hookseal seam) are fired from the extracted
        archive by pkm and deliberately neither deployed nor registered
        (pkm/installer.py _ARCHIVE_METADATA_DIRS) — their absence from the
        chroot is by design, not a payload violation. First observed on the
        ge9b-12 hook-staging members: 45 findings on a correct image."""
        files = {"usr/bin/alpha": b"alpha payload",
                 ".scripts/post_install.sh": b"#!/bin/sh\ntrue\n"}
        _make_archive(self.archives, "alpha", "1.0", "3", files)
        self._register("alpha", "1.0", 3)
        self._manifest("alpha", "1.0", release=3)
        self._deploy({"usr/bin/alpha": b"alpha payload"})  # hooks never land
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertNotIn(".scripts", out)

    def test_claimed_path_absent_fails(self):
        files = {"usr/bin/alpha": b"x", "etc/sysconfig/keep": b"y"}
        _make_archive(self.archives, "alpha", "1.0", "1", files)
        self._register("alpha", "1.0", 1)
        self._manifest("alpha", "1.0", release=1)
        self._deploy({"usr/bin/alpha": b"x"})       # etc/sysconfig never lands
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("claimed path absent: /etc/sysconfig/keep", out)

    def test_modified_etc_file_is_existence_only(self):
        files = {"etc/alpha.conf": b"shipped default"}
        _make_archive(self.archives, "alpha", "1.0", "1", files)
        self._register("alpha", "1.0", 1)
        self._manifest("alpha", "1.0", release=1)
        self._deploy({"etc/alpha.conf": b"config phase rewrote me"})
        rc, out = self._run()
        self.assertEqual(rc, 0, out)

    def test_modified_payload_outside_etc_fails(self):
        files = {"usr/lib/libalpha.so": b"real bytes"}
        _make_archive(self.archives, "alpha", "1.0", "1", files)
        self._register("alpha", "1.0", 1)
        self._manifest("alpha", "1.0", release=1)
        self._deploy({"usr/lib/libalpha.so": b"stale bytes"})
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("content differs from every claiming archive", out)

    def test_bootstrap_twin_shared_path_passes_when_either_matches(self):
        shared_old = b"pass1 build"
        shared_new = b"pass2 build"
        _make_archive(self.archives, "beta-pass1", "2.0", "1",
                      {"usr/lib/libbeta.so": shared_old})
        _make_archive(self.archives, "beta", "2.0", "1",
                      {"usr/lib/libbeta.so": shared_new})
        for n in ("beta-pass1", "beta"):
            self._register(n, "2.0", 1)
            self._manifest(n, "2.0", release=1)
        self._deploy({"usr/lib/libbeta.so": shared_new})  # last-installed wins
        rc, out = self._run()
        self.assertEqual(rc, 0, out)

    def test_missing_db_row_fails(self):
        files = {"usr/bin/gamma": b"g"}
        _make_archive(self.archives, "gamma", "3.0", "1", files)
        self._manifest("gamma", "3.0", release=1)
        self._deploy(files)
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("NO installed row", out)

    def test_missing_manifest_fails(self):
        files = {"usr/bin/delta": b"d"}
        _make_archive(self.archives, "delta", "4.0", "2", files)
        self._register("delta", "4.0", 2)
        self._deploy(files)
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("text manifest var/lib/igos/packages/delta-4.0", out)

    def test_stale_manifest_release_header_fails(self):
        files = {"usr/bin/eps": b"e"}
        _make_archive(self.archives, "eps", "5.0", "7", files)
        self._register("eps", "5.0", 7)
        self._manifest("eps", "5.0", release=6)
        self._deploy(files)
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("PACKAGE RELEASE 6", out)

    def test_excluded_archive_is_skipped(self):
        files = {"usr/bin/zeta": b"z"}
        _make_archive(self.archives, "zeta", "6.0", "9", files)
        # zeta is mirror-only: no row, no manifest, no payload — but excluded.
        _make_archive(self.archives, "alpha", "1.0", "1",
                      {"usr/bin/alpha": b"a"})
        self._register("alpha", "1.0", 1)
        self._manifest("alpha", "1.0", release=1)
        self._deploy({"usr/bin/alpha": b"a"})
        excl = self.chroot / "excludes.txt"
        excl.write_text("zeta-6.0.igos.tar.gz\n")
        rc, out = self._run(["--archive-excludes", str(excl)])
        self.assertEqual(rc, 0, out)

    # -- hook-managed content (pkm D-9/D-9b) -------------------------------

    def _flag_hook_managed(self, name, path):
        """Give the minimal fixture DB a files table and flag (name, path)
        as hook-managed, the shape pkm's installer records at hook time."""
        conn = sqlite3.connect(self.db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(installed)")]
        if "id" not in cols:
            conn.execute("ALTER TABLE installed ADD COLUMN id INTEGER")
            for i, (n,) in enumerate(
                    conn.execute("SELECT name FROM installed").fetchall(), 1):
                conn.execute(
                    "UPDATE installed SET id = ? WHERE name = ?", (i, n))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files (package_id INTEGER, "
            "path TEXT, is_generated INTEGER DEFAULT 0)")
        pkg_id = conn.execute(
            "SELECT id FROM installed WHERE name = ?", (name,)).fetchone()[0]
        conn.execute(
            "INSERT INTO files VALUES (?, ?, 1)", (pkg_id, path))
        conn.commit()
        conn.close()

    def test_hook_managed_payload_is_existence_only(self):
        """The docbook-xml catalog.xml class (first fired ge9b-13): the
        archive seals the pristine pre-hook copy, the package's own sealed
        hook rewrites the live file, pkm records the observation — the gate
        must accept the divergence on existence, exactly like etc/."""
        files = {"usr/share/xml/catalog.xml": b"pristine upstream"}
        _make_archive(self.archives, "alpha", "1.0", "1", files)
        self._register("alpha", "1.0", 1)
        self._manifest("alpha", "1.0", release=1)
        self._deploy({"usr/share/xml/catalog.xml": b"rewritten by own hook"})
        self._flag_hook_managed("alpha", "usr/share/xml/catalog.xml")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("1 hook-managed paths existence-only", out)

    def test_hook_flag_of_another_package_does_not_soften_the_compare(self):
        """Only the OWNING package's recorded observation excuses its path —
        a flag on some other package's row must leave the byte check
        exactly as strict (no cross-package laundering)."""
        files = {"usr/share/xml/catalog.xml": b"pristine upstream"}
        _make_archive(self.archives, "alpha", "1.0", "1", files)
        self._register("alpha", "1.0", 1)
        self._manifest("alpha", "1.0", release=1)
        self._register("beta", "2.0", 1)
        self._manifest("beta", "2.0", release=1)
        _make_archive(self.archives, "beta", "2.0", "1",
                      {"usr/bin/beta": b"b"})
        self._deploy({"usr/share/xml/catalog.xml": b"tampered",
                      "usr/bin/beta": b"b"})
        self._flag_hook_managed("beta", "usr/share/xml/catalog.xml")
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("content differs from every claiming archive", out)


if __name__ == "__main__":
    unittest.main()

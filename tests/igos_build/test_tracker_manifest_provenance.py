#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The source builder stamps its rows with the manifest hash they mirror.

Both tracker lanes write a text manifest and then register a row from the same
material. pkm keys re-registration on installed.manifest_sha256 and treats NULL
as unproven provenance, so a row written without the stamp is re-registered by
the first corpus-wide `pkm import` — which the bash driver fires after every
single package build, over every manifest in the directory, not just the one it
just built. One package rebuild therefore re-registered the whole chroot.

The stamp is asserted on BOTH lanes because they are separate writers with
separate manifest builders: the DESTDIR-staging lane (pkg_manifest) and the
filesystem-diff lane (pkg_manifest_from_diff) the direct_install recipes use. A
fix reaching only one would leave exactly those packages carrying NULL.

reboot_required is asserted for the same reason: the recipe declares it, the
archive's .PKGINFO carries it (tracker _build_pkginfo emits it), and the
archive-install path records it — so a source-built row that dropped it
disagreed with the archive built from the same recipe in the same run.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package  # noqa: E402
from pkm.database import PackageDB, _sha256  # noqa: E402

_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker


class _Logger:
    def __init__(self):
        self.errors, self.warnings, self.infos = [], [], []

    def error(self, msg):
        self.errors.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)

    def info(self, msg):
        self.infos.append(msg)


class _Host(PackageTracker):
    """The real mixin on a host that supplies only the attributes it reads.

    Subclassing rather than binding methods onto a namespace keeps the
    staticmethod/instance-method distinction intact — the mixin has both, and
    hand-binding a staticmethod passes the host in as its first positional
    argument.
    """

    def __init__(self, tmp: Path):
        self.logger = _Logger()
        self.pkg_db = tmp / "packages"
        self.pkg_archives = tmp / "archives"
        self.pkg_staging = tmp / "staging"
        self.sources_dir = None
        for d in (self.pkg_db, self.pkg_archives, self.pkg_staging):
            d.mkdir(parents=True, exist_ok=True)


class TrackerManifestProvenanceTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.host = _Host(self.tmp)
        self.db_path = self.tmp / "pkm.db"

    def tearDown(self):
        self._td.cleanup()

    def test_staged_lane_stamps_the_manifest_hash(self):
        pkg = make_package(name="demo", version="1.0", release=4)
        staged = self.host.pkg_staging / "demo-1.0"
        (staged / "usr" / "bin").mkdir(parents=True)
        (staged / "usr" / "bin" / "demo").write_bytes(b"payload\n")

        self.assertTrue(self.host.pkg_manifest(pkg, staged))
        expected = _sha256(str(self.host.pkg_db / "demo-1.0"))
        self.assertEqual(self.host._pending_pkm_manifest_sha256, expected)

    def test_diff_lane_stamps_the_manifest_hash(self):
        pkg = make_package(name="demo", version="1.0", release=4)
        live = self.tmp / "live" / "usr" / "bin"
        live.mkdir(parents=True)
        target = live / "demo"
        target.write_bytes(b"payload\n")
        st = target.stat()
        after = {str(target): (st.st_size, st.st_mtime_ns, st.st_ctime_ns)}

        self.assertTrue(self.host.pkg_manifest_from_diff(pkg, {}, after))
        expected = _sha256(str(self.host.pkg_db / "demo-1.0"))
        self.assertEqual(self.host._pending_pkm_manifest_sha256, expected)

    def _register(self, pkg, own_paths, hashes, manifest_sha256):
        real = _tracker_mod.PackageDB
        _tracker_mod.PackageDB = (
            lambda *a, **kw: PackageDB(str(self.db_path), root="/"))
        try:
            return self.host._write_pkm_db(
                pkg, list(hashes), hashes, own_paths, manifest_sha256)
        finally:
            _tracker_mod.PackageDB = real

    def _row(self, name):
        db = PackageDB(str(self.db_path), root="/")
        try:
            return db.get_installed(name)
        finally:
            db.close()

    def test_the_registered_row_carries_the_stamp_and_reboot_required(self):
        pkg = make_package(name="demo", version="1.0", release=4, tier="core")
        pkg.reboot_required = True
        sha = "a" * 64
        self.assertTrue(
            self._register(pkg, ["usr/bin/", "usr/bin/demo"],
                           {"usr/bin/demo": "b" * 64}, sha))
        row = self._row("demo")
        self.assertEqual(row["manifest_sha256"], sha)
        self.assertEqual(row["reboot_required"], 1)
        self.assertEqual(row["release"], 4)
        self.assertEqual(row["tier"], "core")

    def test_a_package_that_activates_live_records_zero(self):
        pkg = make_package(name="quiet", version="1.0", release=1)
        self.assertTrue(
            self._register(pkg, ["usr/bin/quiet"],
                           {"usr/bin/quiet": "c" * 64}, "d" * 64))
        self.assertEqual(self._row("quiet")["reboot_required"], 0)

    def test_a_stamped_source_row_is_a_no_op_for_a_later_import(self):
        """The end-to-end property: build, then import, and nothing churns."""
        pkg = make_package(name="demo", version="1.0", release=4, tier="core")
        staged = self.host.pkg_staging / "demo-1.0"
        (staged / "usr" / "bin").mkdir(parents=True)
        payload = staged / "usr" / "bin" / "demo"
        payload.write_bytes(b"payload\n")
        self.assertTrue(self.host.pkg_manifest(pkg, staged))

        live = self.tmp / "live"
        (live / "usr" / "bin").mkdir(parents=True)
        (live / "usr" / "bin" / "demo").write_bytes(b"payload\n")

        self.assertTrue(self._register(
            pkg, self.host._pending_pkm_own_paths,
            self.host._pending_pkm_hashes,
            self.host._pending_pkm_manifest_sha256))

        db = PackageDB(str(self.db_path), root=str(live))
        try:
            self.assertEqual(db.import_manifests(self.host.pkg_db), 0)
            row = db.get_installed("demo")
        finally:
            db.close()
        self.assertEqual(row["install_method"], "source-build")
        self.assertEqual(row["tier"], "core")


if __name__ == "__main__":
    unittest.main()

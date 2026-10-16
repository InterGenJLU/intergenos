#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The source builder's registration sets every /etc baseline to the build's stock.

pkm keeps a configuration file's FIRST baseline across a re-registration on
purpose (PackageDB.add_files: on an installed machine only the upgrade path
moves it, after proving the person did not edit the file). A build root has
no person, and a rebuild re-registers every package it builds again, so the
baseline of an /etc file a release bump changed stayed at the lineage's older
bytes. The squashfs prune then read the new stock as an edit and preserved
it, and the ownership gate refused the image: halt #2 of the R001.3 mint,
2026-10-16 — nvidia r17 and rsyslog r2 registered over the lineage's rows.

What is pinned: a second registration of the same name with a changed /etc
hash leaves the baseline AT THE NEW HASH; a path the build did not hash (a
symbolic link under etc/) keeps the baseline add_files recorded; nothing
outside etc/ is touched.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package  # noqa: E402
from pkm.database import PackageDB  # noqa: E402

_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker

A, B, C, X = "a" * 64, "b" * 64, "c" * 64, "d" * 64


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
    def __init__(self, tmp: Path):
        self.logger = _Logger()
        self.pkg_db = tmp / "packages"
        self.pkg_archives = tmp / "archives"
        self.pkg_staging = tmp / "staging"
        self.sources_dir = None
        for d in (self.pkg_db, self.pkg_archives, self.pkg_staging):
            d.mkdir(parents=True, exist_ok=True)


class TrackerConfigBaselineRebaselineTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.host = _Host(self.tmp)
        self.db_path = self.tmp / "pkm.db"
        # The install root the database hashes against; no /etc file exists
        # there, so every baseline comes from the hashes the build passes.
        self.root = self.tmp / "root"
        self.root.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _register(self, pkg, own_paths, hashes):
        real = _tracker_mod.PackageDB
        _tracker_mod.PackageDB = (
            lambda *a, **kw: PackageDB(str(self.db_path), root=str(self.root)))
        try:
            return self.host._write_pkm_db(
                pkg, [p for p in own_paths if not p.endswith("/")], hashes,
                own_paths, "e" * 64)
        finally:
            _tracker_mod.PackageDB = real

    def _baselines(self):
        db = PackageDB(str(self.db_path), root=str(self.root))
        try:
            return dict(db.conn.execute(
                "SELECT path, original_checksum FROM config_files"))
        finally:
            db.close()

    def test_a_rebuild_moves_the_baseline_to_the_new_stock(self):
        own = ["etc/", "etc/demo.conf", "etc/demo.link", "usr/bin/", "usr/bin/demo"]
        self.assertTrue(self._register(
            make_package(name="demo", version="1.0", release=1), own,
            {"etc/demo.conf": A, "usr/bin/demo": X}))
        self.assertEqual(self._baselines()["etc/demo.conf"], A)

        self.assertTrue(self._register(
            make_package(name="demo", version="1.0", release=2), own,
            {"etc/demo.conf": B, "usr/bin/demo": X}))
        baselines = self._baselines()
        self.assertEqual(baselines["etc/demo.conf"], B,
                         "the rebuild's stock is the baseline, not the first install's")
        # The unhashed link keeps what add_files recorded (NULL: no file in
        # the root to hash), and nothing outside etc/ gained a baseline.
        self.assertIn("etc/demo.link", baselines)
        self.assertIsNone(baselines["etc/demo.link"])
        self.assertNotIn("usr/bin/demo", baselines)
        self.assertTrue(any("config baselines set to this build's stock: 1 file(s)"
                            in m for m in self.host.logger.infos), self.host.logger.infos)

    def test_an_unchanged_stock_is_a_no_op_in_effect(self):
        own = ["etc/", "etc/demo.conf"]
        self.assertTrue(self._register(
            make_package(name="demo", version="1.0", release=1), own,
            {"etc/demo.conf": C}))
        self.assertTrue(self._register(
            make_package(name="demo", version="1.0", release=2), own,
            {"etc/demo.conf": C}))
        self.assertEqual(self._baselines()["etc/demo.conf"], C)

    def test_the_helper_counts_only_hashed_regular_etc_files(self):
        db = PackageDB(str(self.db_path), root=str(self.root))
        try:
            pid = db.add_installed(name="demo", version="1.0", release=1,
                                   tier="core", description="d")
            db.add_files(pid, ["etc/", "etc/a.conf", "etc/b.conf", "etc/link",
                               "usr/bin/demo"],
                         hashes={"etc/a.conf": A, "etc/b.conf": A, "usr/bin/demo": X})
            written = PackageTracker._rebaseline_config_files(
                db, ["etc/", "etc/a.conf", "etc/b.conf", "etc/link", "usr/bin/demo"],
                {"etc/a.conf": B, "etc/b.conf": C, "usr/bin/demo": B})
            self.assertEqual(written, 2)
            rows = dict(db.conn.execute(
                "SELECT path, original_checksum FROM config_files"))
            self.assertEqual((rows["etc/a.conf"], rows["etc/b.conf"]), (B, C))
            self.assertEqual(PackageTracker._rebaseline_config_files(db, [], {}), 0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

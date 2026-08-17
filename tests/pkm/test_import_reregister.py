#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm import re-registers on a version bump (pre-GE pkm hardening).

Regression for the metadata-not-updated-on-re-register class (framework §3.5):
`pkm import` runs at end-of-build to register the install set, but it used to
skip ANY name already in the DB. A direct_install (filesystem-diff) version bump
rewrites the package's text manifest + on-disk files, yet the old DB row — the
version string AND the files/checksums it points at — rode unchanged, so
`pkm verify` failed on the shipped system (on-disk files = new version, DB hashes
= old). The fix re-imports a manifest whose version differs from the DB row, and
keeps the no-op skip for an unchanged version.
"""

import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB


class ImportReRegisterTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.mdir = self.tmp / "manifests"
        self.mdir.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _write_pkg(self, name, version, files):
        """Write the on-disk files (version-stamped content) + the sole text
        manifest for <name>, replacing any prior-version manifest the way the
        build's stale-archive/manifest sweep does."""
        for rel in files:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"{name}-{version}:{rel}")  # content varies by version
        for stale in self.mdir.glob(f"{name}-*"):
            stale.unlink()
        body = [f"PACKAGE NAME: {name}-{version}",
                f"PACKAGE VERSION: {version}", "FILE LIST:", *files]
        (self.mdir / f"{name}-{version}").write_text("\n".join(body) + "\n")

    def test_version_bump_reregisters_and_refreshes_files(self):
        self._write_pkg("foo", "1.0", ["usr/bin/foo"])
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        self.assertEqual(self.db.get_installed("foo")["version"], "1.0")

        # Bump: new on-disk content + a new-version manifest (old one swept).
        self._write_pkg("foo", "2.0", ["usr/bin/foo"])
        self.db.import_manifests(self.mdir)

        row = self.db.get_installed("foo")
        self.assertEqual(row["version"], "2.0",
                         "version-bump manifest must re-register, not be skipped")
        # Files refreshed: the DB checksum now matches the NEW on-disk content, so
        # verify is clean. Under the old skip-existing behavior the stale 1.0
        # checksum would mismatch the 2.0 file and verify would flag it modified.
        verify = self.db.verify_package("foo")
        self.assertEqual(verify["missing"], [])
        self.assertEqual(verify["modified"], [])

    def test_unchanged_version_is_a_noop(self):
        self._write_pkg("bar", "1.0", ["usr/bin/bar"])
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        id1 = self.db.get_installed("bar")["id"]
        # Re-import the identical manifest: skipped, row stable (same id), 0 imported.
        self.assertEqual(self.db.import_manifests(self.mdir), 0)
        self.assertEqual(self.db.get_installed("bar")["id"], id1)


if __name__ == "__main__":
    unittest.main()

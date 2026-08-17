#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every text-manifest writer states the package release.

The reader-side rule (tests/pkm/test_import_release_preservation.py) stops a
header-less manifest from RESETTING a known release. This is the other half:
the manifests this builder writes must state the release outright, so a row
re-registered from them is truthful on its own evidence rather than on what a
previous writer happened to leave in the database.

Both tracking paths are covered — the DESTDIR-staged writer and the
direct_install filesystem-diff writer. They are separate code paths that
produce the same artifact, and a fix applied to only one of them is exactly
the kind of half-covered change that leaves the defect alive on the lane
nobody tested.

The assertion runs the rendered bytes back through pkm's own
``_parse_manifest`` rather than pattern-matching the header text: what matters
is not that the line exists but that the parser the importer uses extracts the
release from it.
"""

from __future__ import annotations

import importlib
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from .factories import make_package  # noqa: E402
from pkm.database import _parse_manifest  # noqa: E402

_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker


class ManifestReleaseHeaderTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.pkg_db = self.tmp / "pkgdb"
        self.pkg_db.mkdir()
        self.staging = self.tmp / "staging"
        (self.staging / "usr" / "bin").mkdir(parents=True)
        (self.staging / "usr" / "bin" / "demo").write_text("payload")

        self.tracker = PackageTracker()
        self.tracker.logger = logging.getLogger("test_manifest_release_header")
        self.tracker.pkg_db = self.pkg_db

    def tearDown(self):
        self._td.cleanup()

    def _parsed(self, pkg):
        manifest = self.pkg_db / f"{pkg.name}-{pkg.version}"
        self.assertTrue(manifest.is_file(), "the writer produced no manifest")
        return _parse_manifest(manifest.read_text())

    def test_staged_writer_states_the_release(self):
        pkg = make_package(name="demo", version="1.0", release=6)
        self.assertTrue(self.tracker.pkg_manifest(pkg, self.staging))

        meta = self._parsed(pkg)
        self.assertEqual(meta.get("release"), 6,
                         "the DESTDIR-staged manifest must state the release "
                         "the recipe declares")
        self.assertEqual(meta["version"], "1.0")

    def test_diff_writer_states_the_release(self):
        """The direct_install lane emits the same header.

        pkg_manifest_from_diff derives its file set from before/after snapshots
        of the live root, so the fixture hands it an empty 'before' and an
        'after' holding one real file it can stat and hash.
        """
        live = self.tmp / "live"
        (live / "usr" / "bin").mkdir(parents=True)
        payload = live / "usr" / "bin" / "demo-direct"
        payload.write_text("payload")

        st = payload.stat()
        before: dict[str, tuple[int, int, int]] = {}
        after = {str(payload): (st.st_size, st.st_mtime_ns, st.st_ctime_ns)}

        pkg = make_package(name="demo-direct", version="2.0", release=4,
                           direct_install=True)
        self.assertTrue(
            self.tracker.pkg_manifest_from_diff(pkg, before, after))

        meta = self._parsed(pkg)
        self.assertEqual(meta.get("release"), 4,
                         "the direct_install manifest must state the release "
                         "too — one covered lane is not a fix")

    def test_release_is_not_pinned_to_the_schema_default(self):
        """A release of 1 proves nothing; the header must track the recipe."""
        first = make_package(name="tracked", version="1.0", release=1)
        self.assertTrue(self.tracker.pkg_manifest(first, self.staging))
        self.assertEqual(self._parsed(first).get("release"), 1)

        bumped = make_package(name="tracked", version="1.0", release=9)
        self.assertTrue(self.tracker.pkg_manifest(bumped, self.staging))
        self.assertEqual(self._parsed(bumped).get("release"), 9,
                         "the header must follow the recipe, not a constant")


if __name__ == "__main__":
    unittest.main()

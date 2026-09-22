#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Downloading a new release must not destroy the one it replaces.

The mirror publishes one filename per version, so before the cache learned to
name a file for the BUILD it holds, every release of a version landed on one
path: fetching release 8 wrote over the only local copy of release 7, and the
pre-upgrade snapshot then had nothing to copy. Measured on an installed
machine 2026-09-19: the cached forge-1.0.0.igos.tar.gz carried pkgrel 241
before `pkm upgrade forge` and 245 after it, and the upgrade reported that no
pre-upgrade copy of 241 was available.

These cases run the real download path twice against one scratch cache — a
release-only upgrade, which is the shape the rollback needs — and read what
is left on disk.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pkm.repo as repo_mod
from pkm.archive_names import archive_filename
from pkm.repo import RepoManager

PUBLISHED = "demo-1.0.igos.tar.gz"   # what the mirror serves, release-less


class TestTheReplacedReleaseSurvives(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cache = Path(self._td.name)
        (self.cache / "partial").mkdir()
        self.pkgcache = self.cache / "packages"
        self.pkgcache.mkdir()
        self.mgr = RepoManager.__new__(RepoManager)
        self.mgr.repos = {"r": {"url": "https://mirror.example/"}}

    def tearDown(self):
        self._td.cleanup()

    def _fetch(self, release, body):
        """One real download_package call for demo at the given release."""
        entry = {"filename": PUBLISHED, "version": "1.0", "release": release,
                 "sha256": hashlib.sha256(body).hexdigest(), "repo": "r"}
        with patch.object(repo_mod, "REPO_CACHE_DIR", self.cache), \
             patch.object(repo_mod, "REPO_PKG_CACHE", self.pkgcache), \
             patch.object(self.mgr, "pkg_cache", return_value=self.pkgcache), \
             patch.object(self.mgr, "get_package", return_value=entry), \
             patch.object(self.mgr, "_mirror_urls_for_pkg",
                          return_value=["https://mirror.example/" + PUBLISHED]), \
             patch.object(self.mgr, "_download",
                          side_effect=lambda url, dst, **_kw:
                              Path(dst).write_bytes(body)):
            return self.mgr.download_package("demo")

    def _cached(self):
        return sorted(p.name for p in self.pkgcache.iterdir() if p.is_file())

    def test_the_previous_release_is_still_on_disk(self):
        ok, first = self._fetch(7, b"release seven bytes\n")
        self.assertTrue(ok, first)
        ok, second = self._fetch(8, b"release eight bytes\n")
        self.assertTrue(ok, second)
        self.assertEqual(
            self._cached(),
            ["demo-1.0-7.igos.tar.gz", "demo-1.0-8.igos.tar.gz"],
            "the release being replaced was overwritten; a pre-upgrade "
            "snapshot has nothing to copy",
        )

    def test_each_cached_file_holds_its_own_release_bytes(self):
        self._fetch(7, b"release seven bytes\n")
        self._fetch(8, b"release eight bytes\n")
        self.assertEqual(
            (self.pkgcache / archive_filename("demo", "1.0", 7)).read_bytes(),
            b"release seven bytes\n")
        self.assertEqual(
            (self.pkgcache / archive_filename("demo", "1.0", 8)).read_bytes(),
            b"release eight bytes\n")

    def test_fetching_the_same_release_twice_uses_the_cache(self):
        body = b"release seven bytes\n"
        self._fetch(7, body)
        ok, path = self._fetch(7, body)
        self.assertTrue(ok, path)
        self.assertEqual(self._cached(), ["demo-1.0-7.igos.tar.gz"])


if __name__ == "__main__":
    unittest.main()

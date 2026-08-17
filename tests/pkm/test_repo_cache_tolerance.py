#!/usr/bin/env python3
"""Regression test for the non-root read-path cache-mkdir crash (PI-E-cache).

Authored 2026-06-18. Before this fix, `pkm search <term>` run as a non-root
user on a fresh install (no /var/cache/pkm yet) blew up with a raw
PermissionError traceback from RepoManager.__init__'s eager cache mkdir —
the scary, opaque output the PRIME DIRECTIVE rejects, and the same class the
cli.py root gate (PKM_MUTATING_COMMANDS) and the DB-open PermissionError
handler already cover for two other surfaces. This was the missed third
surface.

Coverage:
  - Non-root / no-cache: RepoManager() must NOT raise; cache_ready is False;
    the read path (search / has_synced_index) degrades cleanly to "nothing
    synced", no crash.
  - Root / writable cache: RepoManager() creates both cache dirs and
    cache_ready is True (the create+chmod still happens for update/install,
    which are root-gated upstream).
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pkm.repo
from pkm.repo import RepoManager


class TestRepoManagerCacheTolerance(unittest.TestCase):
    def test_nonroot_no_cache_does_not_crash(self):
        # Simulate a non-root read on a fresh system: the cache dir doesn't
        # exist and cannot be created (root owns /var/cache). Force the exact
        # failure regardless of the uid the test runs as (the build chroot
        # runs tests as root) by denying mkdir during construction.
        def deny(self, *a, **k):
            raise PermissionError(13, "Permission denied", str(self))

        # Point the cache constants at a never-created tmp path (the fresh-
        # system shape): on an installed InterGenOS host the REAL
        # /var/cache/pkm holds a synced index, and the read path resolves the
        # module constants at call time — without this the test reads host
        # state and has_synced_index() is legitimately True.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "pkm"  # never created; mkdir is denied below
            with patch.object(pkm.repo, "REPO_CACHE_DIR", base), \
                 patch.object(pkm.repo, "REPO_DB_CACHE", base / "db"), \
                 patch.object(pkm.repo, "REPO_PKG_CACHE", base / "packages"):
                with patch.object(Path, "mkdir", deny):
                    rm = RepoManager()  # MUST NOT raise

                self.assertFalse(rm.cache_ready)
                # Read paths degrade cleanly — no crash, no results, and the
                # caller can detect "nothing synced" to advise
                # `sudo pkm update`.
                self.assertEqual(rm.search("anything"), [])
                self.assertFalse(rm.has_synced_index())

    def test_root_path_creates_cache(self):
        # Writable cache (the root update/install path): both dirs created,
        # cache_ready True. Confirms the fix did not regress normal creation.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "pkm"
            with patch.object(pkm.repo, "REPO_CACHE_DIR", base), \
                 patch.object(pkm.repo, "REPO_DB_CACHE", base / "db"), \
                 patch.object(pkm.repo, "REPO_PKG_CACHE", base / "packages"):
                rm = RepoManager()
                self.assertTrue(rm.cache_ready)
                self.assertTrue((base / "db").is_dir())
                self.assertTrue((base / "packages").is_dir())


if __name__ == "__main__":
    unittest.main()

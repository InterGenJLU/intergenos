#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A11 regression: a rejected cached index is surfaced, never swallowed.

_ensure_synced loads cached repo indexes lazily. It wrapped _parse_index —
where the fail-closed gates live (anti-rollback / freshness / Valid-Until /
min_pkm_version) — in `except Exception: pass`. So when one of those gates
REJECTED a stale or rolled-back cached index, the repo silently vanished
from self.indexes and every subsequent package lookup read "not found"
with no explanation: a rollback attack or an expired index degraded into a
confusing empty repo.

Fixed contract:
  * IndexFormatError (a deliberate fail-closed rejection) is caught
    distinctly and surfaced LOUDLY on stderr, naming the repo + reason +
    the `pkm sync` remediation; the cached artifact is LEFT in place for
    inspection (mirrors sync()'s L-020 handler);
  * a genuinely-unexpected parse error still soft-skips that repo (others
    load) but is LOGGED, never a silent pass.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pkm.repo as repo_mod
from pkm.repo import RepoManager, IndexFormatError


class CachedIndexRejectSurfaceTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cache = Path(self._td.name) / "db"
        self.cache.mkdir(parents=True)
        # Cached artifacts for one repo "intergenos" exist on disk.
        self.db_path = self.cache / "intergenos.db"
        self.sig_path = self.cache / "intergenos.db.sig"
        self.db_path.write_bytes(b"cached-index-bytes")
        self.sig_path.write_bytes(b"sig")
        # A keyring file that .exists().
        self.keyring = Path(self._td.name) / "trusted.gpg"
        self.keyring.write_bytes(b"keyring")

        # Build a RepoManager without running __init__'s filesystem setup.
        self.mgr = RepoManager.__new__(RepoManager)
        self.mgr.indexes = {}
        self.mgr.repos = {
            "intergenos": {"url": "https://repo.intergenos.org/x86_64/current"}
        }

    def tearDown(self):
        self._td.cleanup()

    def _run_ensure_synced(self, parse_side_effect):
        buf = io.StringIO()
        with patch.object(repo_mod, "REPO_DB_CACHE", self.cache), \
             patch.object(repo_mod, "GPG_KEYRING", self.keyring), \
             patch.object(self.mgr, "_verify_signature", return_value=True), \
             patch.object(self.mgr, "_parse_index",
                          side_effect=parse_side_effect), \
             redirect_stderr(buf):
            self.mgr._ensure_synced()
        return buf.getvalue()

    def test_index_format_error_is_surfaced_loud_and_artifact_kept(self):
        err = self._run_ensure_synced(
            IndexFormatError("anti-rollback: cached generated 2026-06-10 is "
                             "older than last-seen 2026-06-15")
        )
        # Loud, not swallowed.
        self.assertNotEqual(err.strip(), "", "rejection must not be silent")
        self.assertIn("REJECTED", err)
        self.assertIn("intergenos", err)
        self.assertIn("anti-rollback", err)  # the gate's reason carried through
        self.assertIn("pkm sync", err)        # remediation (sync is the canonical index-refresh command)
        # The repo did not load (fail-closed), but the cached artifact is
        # LEFT in place for operator inspection.
        self.assertNotIn("intergenos", self.mgr.indexes)
        self.assertTrue(self.db_path.exists())
        self.assertTrue(self.sig_path.exists())

    def test_unexpected_error_is_logged_not_silent(self):
        err = self._run_ensure_synced(ValueError("totally unexpected"))
        self.assertNotEqual(err.strip(), "", "unexpected error must be logged")
        self.assertIn("could not be loaded", err)
        self.assertIn("ValueError", err)
        self.assertIn("intergenos", err)
        self.assertNotIn("intergenos", self.mgr.indexes)

    def test_clean_index_loads_silently(self):
        sentinel = object()
        buf = io.StringIO()
        with patch.object(repo_mod, "REPO_DB_CACHE", self.cache), \
             patch.object(repo_mod, "GPG_KEYRING", self.keyring), \
             patch.object(self.mgr, "_verify_signature", return_value=True), \
             patch.object(self.mgr, "_parse_index", return_value=sentinel), \
             redirect_stderr(buf):
            self.mgr._ensure_synced()
        # No noise on the happy path; the index loaded.
        self.assertEqual(buf.getvalue(), "")
        self.assertIs(self.mgr.indexes["intergenos"], sentinel)


if __name__ == "__main__":
    unittest.main()

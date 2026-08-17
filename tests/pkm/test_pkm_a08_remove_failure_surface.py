#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A08 regression: remove() surfaces unlink failures, never swallows them.

Pre-A08, PackageRemover.remove caught OSError/PermissionError per file with
a bare `pass` ("best effort"), then returned (True, "Removed N files") — so
a file that could not be unlinked stayed on disk while the package's DB row
was removed, and the user saw a clean success over a real FS/DB
inconsistency.

Fixed contract:
  * unlink failures are collected and surfaced — in the returned message
    AND via reporter.warn (the channel the CLI actually shows on success);
  * the success done() line is qualified when files were left on disk;
  * the call still returns True (the package IS removed from the DB; the
    remove genuinely partially succeeded). Returning False would make
    reinstall (cli.py:1097) hard-abort and autoremove (cli.py:2174) bail
    the loop on a single stuck file — a strictly worse outcome. So the
    finding is closed by *surfacing*, not by flipping the bool.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkm.database import PackageDB
from pkm.remover import PackageRemover


class RemoveFailureSurfaceTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "root"
        self.root.mkdir(parents=True)
        self.db = PackageDB(Path(self._td.name) / "pkm.db", root=str(self.root))
        # Install a package with two tracked payload files on disk.
        self.files = ["usr/bin/keepme", "usr/bin/stuckme"]
        for rel in self.files:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("payload\n")
        pkg_id = self.db.add_installed("demo", "1.0.0", release=1, tier="base")
        self.db.add_files(pkg_id, self.files + ["usr/bin/", "usr/"])

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _remove_with_one_unlink_failure(self, reporter=None):
        """Patch os.remove so exactly the 'stuckme' file fails to unlink."""
        stuck_abs = str(self.root / "usr/bin/stuckme")
        real_remove = os.remove

        def fake_remove(path, *a, **k):
            if str(path) == stuck_abs:
                raise PermissionError(13, "Operation not permitted")
            return real_remove(path, *a, **k)

        remover = PackageRemover(self.db, root=str(self.root))
        with patch("pkm.remover.os.remove", side_effect=fake_remove):
            return remover.remove("demo", force=True, reporter=reporter)

    def test_failure_surfaced_in_message_and_returns_true(self):
        ok, msg = self._remove_with_one_unlink_failure()
        # Still True — the DB row IS removed; the remove partially succeeded.
        self.assertTrue(ok)
        # The stuck file is named loudly in the message (not swallowed).
        self.assertIn("WARNING", msg)
        self.assertIn("usr/bin/stuckme", msg)
        self.assertIn("Operation not permitted", msg)

    def test_stuck_file_remains_db_row_gone(self):
        self._remove_with_one_unlink_failure()
        # The unlinkable file is still on disk (honest report — it remained).
        self.assertTrue((self.root / "usr/bin/stuckme").exists())
        # The other file was removed.
        self.assertFalse((self.root / "usr/bin/keepme").exists())
        # The DB row is gone (package considered uninstalled by pkm).
        self.assertIsNone(self.db.get_installed("demo"))

    def test_reporter_warn_is_called_with_stuck_file(self):
        reporter = MagicMock()
        ok, _ = self._remove_with_one_unlink_failure(reporter=reporter)
        self.assertTrue(ok)
        # The CLI success path relies on the reporter, not the message —
        # so the warn MUST fire there.
        reporter.warn.assert_called_once()
        warn_arg = reporter.warn.call_args.args[0]
        self.assertIn("usr/bin/stuckme", warn_arg)
        # And the success line is qualified, not a bare green "Removed".
        done_arg = reporter.done.call_args.args[0]
        self.assertIn("left on disk", done_arg)

    def test_clean_remove_has_no_warning(self):
        # No unlink failure -> no WARNING, no reporter.warn, plain done().
        reporter = MagicMock()
        remover = PackageRemover(self.db, root=str(self.root))
        ok, msg = remover.remove("demo", force=True, reporter=reporter)
        self.assertTrue(ok)
        self.assertNotIn("WARNING", msg)
        reporter.warn.assert_not_called()
        self.assertNotIn("left on disk", reporter.done.call_args.args[0])
        # Both payload files actually gone.
        self.assertFalse((self.root / "usr/bin/stuckme").exists())
        self.assertFalse((self.root / "usr/bin/keepme").exists())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A16 regression: add_files raises on a genuine IntegrityError.

The per-file INSERT into the files table caught sqlite3.IntegrityError with
`print(WARN); continue` INSIDE the installer's atomic BEGIN/COMMIT. Because
INSERT OR REPLACE already absorbs the benign duplicate case and
files.package_id is `NOT NULL REFERENCES installed(id)` (FKs enforced), any
IntegrityError that surfaces is a GENUINE constraint violation — and
swallowing it left the file deployed on disk with no ownership row while the
package still committed as installed (orphaned-after-remove).

Fixed: add_files re-raises the IntegrityError so the atomic install rolls
back fail-closed and standalone callers abort before commit.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB


class AddFilesIntegrityTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "root"
        self.root.mkdir()
        self.db = PackageDB(Path(self._td.name) / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_bogus_package_id_raises_not_swallowed(self):
        # package_id 999999 does not exist in installed -> FK violation.
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.db.add_files(999999, ["usr/bin/orphan"])
        # The raised error names the offending path + package (loud).
        self.assertIn("usr/bin/orphan", str(ctx.exception))
        self.assertIn("999999", str(ctx.exception))

    def test_no_orphan_row_committed_on_failure(self):
        try:
            self.db.add_files(999999, ["usr/bin/orphan"])
        except sqlite3.IntegrityError:
            pass
        # Fail-closed: NO ownership row was committed for the bogus package.
        rows = self.db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE package_id = ?", (999999,)
        ).fetchone()[0]
        self.assertEqual(rows, 0)

    def test_valid_package_id_still_records_files(self):
        # No regression: a real install path still records ownership rows.
        pkg_id = self.db.add_installed("demo", "1.0.0", tier="core")
        self.db.add_files(pkg_id, ["usr/bin/demo", "usr/share/demo/data"])
        rows = self.db.conn.execute(
            "SELECT path FROM files WHERE package_id = ? ORDER BY path",
            (pkg_id,),
        ).fetchall()
        paths = [r[0] for r in rows]
        self.assertIn("usr/bin/demo", paths)
        self.assertIn("usr/share/demo/data", paths)


if __name__ == "__main__":
    unittest.main()

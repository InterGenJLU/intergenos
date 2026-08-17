#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A31 regression: read-only commands tolerate a behind-schema DB.

Found by real-install testing (2026-06-16, .227): pkm 0.2.0 added the `degraded`
column (A25) with an ALTER-TABLE migration, but migrations only run on a
read-WRITE open. A read-only command (`pkm list`, non-root — or root via a
read-only subcommand) opens the DB immutable and CANNOT migrate it, so on a
system upgraded from a pre-A25 pkm `list_installed` crashed with
`sqlite3.OperationalError: no such column: degraded` until some later root
command happened to run the migration. That is the ugly-crash class the audit
targets.

Fix: list_installed (and the read path generally) selects schema-tolerantly via
_col() — a column absent on an un-migrated DB yields NULL instead of raising.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB


class SchemaTolerantReadTest(unittest.TestCase):
    def _old_schema_db(self, path):
        """A minimal `installed` table as an OLDER pkm would have created it —
        WITHOUT the `degraded` column (and without other newer columns)."""
        c = sqlite3.connect(str(path))
        c.execute(
            "CREATE TABLE installed ("
            "id INTEGER PRIMARY KEY, name TEXT, version TEXT, "
            "release INTEGER DEFAULT 1, tier TEXT, description TEXT, "
            "install_reason TEXT DEFAULT 'manual')"
        )
        c.execute(
            "INSERT INTO installed (name, version, release, tier, description) "
            "VALUES ('foo', '1.0', 2, 'core', 'a package')"
        )
        c.commit()
        c.close()

    def test_list_installed_tolerates_missing_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            dbp = Path(td) / "pkm.db"
            self._old_schema_db(dbp)
            # Read-only open: cannot run the migration → `degraded` stays absent.
            db = PackageDB(dbp, read_only=True)
            try:
                rows = db.list_installed()          # must NOT raise
            finally:
                db.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "foo")
            self.assertEqual(rows[0]["release"], 2)
            self.assertIsNone(rows[0]["degraded"])  # absent → NULL → healthy

    def test_tier_filter_also_tolerates_missing_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            dbp = Path(td) / "pkm.db"
            self._old_schema_db(dbp)
            db = PackageDB(dbp, read_only=True)
            try:
                rows = db.list_installed(tier="core")
            finally:
                db.close()
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["degraded"])

    def test_full_schema_db_still_reports_degraded(self):
        # A normal (read-write, freshly created) DB has the full schema +
        # migrations, so `degraded` is a real column and round-trips.
        with tempfile.TemporaryDirectory() as td:
            dbp = Path(td) / "pkm.db"
            db = PackageDB(dbp, create_if_missing=True,
                           root=str(Path(td) / "root"))
            try:
                db.add_installed("bar", "2.0", release=1, tier="core")
                db.mark_degraded("bar", "hook-x failed")
                rows = db.list_installed()
            finally:
                db.close()
            row = next(r for r in rows if r["name"] == "bar")
            self.assertEqual(row["degraded"], "hook-x failed")


if __name__ == "__main__":
    unittest.main()

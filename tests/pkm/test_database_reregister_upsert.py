# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""add_installed UPSERTS on a name collision — the version-bump re-register
primitive (framework §3.5 step 2 / the go 1.26.2 -> 1.26.4 direct_install class).

The stale-version-row bug this guards against lived in import_manifests'
skip-if-exists path and is FIXED there (pkm/database.py; regression test
tests/pkm/test_import_reregister.py). This test pins the DB PRIMITIVE every
re-register path ultimately rests on: add_installed is `INSERT OR REPLACE` on the
UNIQUE(name) column, and files reference installed(id) ON DELETE CASCADE — so a
version-bump re-register REPLACES the version row (never leaves a stale one) AND
mints a fresh row id whose cascade drops the old package's file rows/checksums.
A future refactor that swapped the upsert for a bare INSERT or a skip-if-exists
guard would resurrect the stale-row bug; these assertions catch that.

The re-registering calls below pass `replace_existing=True`: the cascade they
pin is unchanged, but a caller now has to declare it (the destructive contract
on add_installed — tests/pkm/test_add_installed_destructive_contract.py).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB


class AddInstalledUpsertTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = PackageDB(Path(self.tmp) / "t.db")

    def tearDown(self):
        self.db.close()

    def test_version_bump_replaces_row_and_cascades_old_files(self):
        # Register go 1.26.2 with its files (the pre-bump state).
        pid1 = self.db.add_installed(name="go", version="1.26.2", release=1,
                                     install_method="source-build")
        self.db.add_files(pid1, ["usr/bin/go", "usr/lib/go/VERSION"],
                          hashes={"usr/lib/go/VERSION": "a" * 64})

        # Re-register the SAME name at 1.26.4 (the version-bump rebuild).
        pid2 = self.db.add_installed(name="go", version="1.26.4", release=1,
                                     install_method="source-build",
                                     replace_existing=True)
        self.db.add_files(pid2, ["usr/bin/go", "usr/lib/go/VERSION"],
                          hashes={"usr/lib/go/VERSION": "b" * 64})

        # The version row is REPLACED, not stale.
        row = self.db.get_installed("go")
        self.assertIsNotNone(row)
        self.assertEqual(row["version"], "1.26.4",
                         "version-bump re-register must replace the row, "
                         "not leave the stale 1.26.2")

        # INSERT OR REPLACE mints a new id, and the old file rows cascaded away.
        self.assertNotEqual(pid1, pid2, "REPLACE must mint a new row id")
        old_files = self.db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE package_id = ?", (pid1,)
        ).fetchone()[0]
        self.assertEqual(old_files, 0,
                         "old package's file rows must cascade away on REPLACE")
        # Exactly one live installed row for the name.
        n = self.db.conn.execute(
            "SELECT COUNT(*) FROM installed WHERE name = 'go'"
        ).fetchone()[0]
        self.assertEqual(n, 1, "there must be exactly one 'go' row, not two")

    def test_unchanged_version_reregister_keeps_single_row(self):
        pid1 = self.db.add_installed(name="foo", version="1.0", release=1)
        pid2 = self.db.add_installed(name="foo", version="1.0", release=1,
                                     replace_existing=True)
        self.assertNotEqual(pid1, pid2)  # REPLACE still mints a new id
        n = self.db.conn.execute(
            "SELECT COUNT(*) FROM installed WHERE name = 'foo'"
        ).fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(self.db.get_installed("foo")["version"], "1.0")


if __name__ == "__main__":
    unittest.main()

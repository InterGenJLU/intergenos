#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""add_installed destroys a package's payload rows; the call site must say so.

`PackageDB.add_installed` is an `INSERT OR REPLACE` on `UNIQUE(name)`, and both
child tables declare `REFERENCES installed(id) ON DELETE CASCADE`. So
re-registering a name that is already installed does not update a row — it
DELETES the row, and with it every file-ownership row and every dependency row
the package had, then inserts a fresh one under a new id.

Measured 2026-07-30 against a copy of a real installed system's database
(972 packages): re-registering `mariadb` dropped 17,806 file rows and 9 depends
rows in a single statement, and moved its 16 config_files baselines to
package_id NULL.

Every production caller happens to re-add both sets immediately, so no live
defect exists today. The hazard is the shape: the destruction is invisible at
the call site, it reports nothing, and a caller that registers without re-adding
deregisters a package's entire payload while the package stays listed as
installed and its files stay on disk. `pkm verify` then has nothing to check and
`pkm remove` cannot unlink what it no longer owns.

The contract these tests pin: the destructive case is refused unless the caller
declares it with `replace_existing=True`, the refusal happens before anything is
written, a declared replace still cascades exactly as it always did (the
behaviour is documented, not changed), and the forensic trace records how many
rows the cascade took. `config_files` is deliberately asymmetric — it declares
`ON DELETE SET NULL`, which is what preserves a config-protect baseline across a
plain reinstall — and that asymmetry is pinned here too, because a future
schema change that made it CASCADE would silently re-baseline every user-edited
/etc file on the next re-register.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pkm.database import PackageDB


class AddInstalledDestructiveContractTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir()
        self.db = PackageDB(str(Path(self.tmp.name) / "pkm.db"),
                            root=str(self.root))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _register(self, name="demo", version="1.0", files=("usr/bin/demo",),
                  deps=(("libc", "runtime"),), **kw):
        pkg_id = self.db.add_installed(name=name, version=version, **kw)
        if files:
            self.db.add_files(pkg_id, list(files),
                              hashes={f: "a" * 64 for f in files})
        if deps:
            self.db.add_depends(pkg_id, list(deps))
        return pkg_id

    def _counts(self, pkg_id):
        f = self.db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE package_id = ?",
            (pkg_id,)).fetchone()[0]
        d = self.db.conn.execute(
            "SELECT COUNT(*) FROM depends WHERE package_id = ?",
            (pkg_id,)).fetchone()[0]
        return f, d

    # -- the gate --------------------------------------------------------

    def test_undeclared_reregister_is_refused(self):
        """A caller that does not declare the destruction does not get it."""
        pkg_id = self._register(files=("usr/bin/demo", "usr/lib/demo.so"))
        self.assertEqual(self._counts(pkg_id), (2, 1))

        with self.assertRaises(ValueError) as ctx:
            self.db.add_installed(name="demo", version="2.0")

        msg = str(ctx.exception)
        self.assertIn("demo", msg)
        self.assertIn("replace_existing", msg,
                      "the refusal must name the parameter that allows it")
        self.assertIn("files", msg)
        self.assertIn("depends", msg)

    def test_refusal_destroys_nothing(self):
        """The guard runs before the INSERT — a refused call is a no-op.

        A guard that fired after the statement would report the hazard while
        having already caused it.
        """
        pkg_id = self._register(files=("usr/bin/demo", "usr/lib/demo.so"),
                                deps=(("libc", "runtime"), ("zlib", "runtime")))
        with self.assertRaises(ValueError):
            self.db.add_installed(name="demo", version="2.0")

        row = self.db.get_installed("demo")
        self.assertEqual(row["id"], pkg_id, "the row id must be untouched")
        self.assertEqual(row["version"], "1.0", "the row must not be replaced")
        self.assertEqual(self._counts(pkg_id), (2, 2),
                         "no child row may be dropped by a refused call")

    def test_registering_an_unknown_name_needs_no_declaration(self):
        """The default is the safe case: nothing exists, nothing is destroyed."""
        pkg_id = self.db.add_installed(name="fresh", version="1.0")
        self.assertIsNotNone(pkg_id)
        self.assertEqual(self.db.get_installed("fresh")["version"], "1.0")

    # -- the declared destructive path -----------------------------------

    def test_declared_replace_still_cascades(self):
        """The behaviour is documented, not changed: the children still go.

        This mirrors the measured leg (mariadb: 17,806 files + 9 depends) at
        test scale. A caller that declares the replace must re-add both sets;
        this test deliberately does not, so the cascade is visible.
        """
        old_id = self._register(files=("usr/bin/demo", "usr/lib/demo.so"),
                                deps=(("libc", "runtime"), ("zlib", "runtime")))
        new_id = self.db.add_installed(name="demo", version="2.0",
                                       replace_existing=True)

        self.assertNotEqual(old_id, new_id, "REPLACE mints a new row id")
        self.assertEqual(self._counts(old_id), (0, 0),
                         "the old id's children cascade away")
        self.assertEqual(self._counts(new_id), (0, 0),
                         "and they are NOT carried onto the new id")
        self.assertEqual(self.db.get_installed("demo")["version"], "2.0")
        n = self.db.conn.execute(
            "SELECT COUNT(*) FROM installed WHERE name = 'demo'"
        ).fetchone()[0]
        self.assertEqual(n, 1, "exactly one row for the name, never two")

    def test_trace_records_what_the_cascade_took(self):
        """The count is the only record that the payload rows existed.

        Once the cascade has run, nothing in the database says how many rows it
        removed, so the trace event is where an audit can see a caller that
        replaced and never re-added.
        """
        old_id = self._register(files=("usr/bin/demo", "usr/lib/demo.so",
                                       "usr/share/demo/x"),
                                deps=(("libc", "runtime"), ("zlib", "runtime")))
        with mock.patch("pkm.database._emit_db_event") as emit:
            self.db.add_installed(name="demo", version="2.0",
                                  replace_existing=True)

        self.assertEqual(emit.call_count, 1)
        fields = emit.call_args.kwargs
        self.assertEqual(fields["replaced_pkg_id"], old_id)
        self.assertEqual(fields["cascaded_files"], 3)
        self.assertEqual(fields["cascaded_depends"], 2)

    def test_fresh_registration_reports_no_cascade(self):
        """A first registration must not claim it destroyed anything."""
        with mock.patch("pkm.database._emit_db_event") as emit:
            self.db.add_installed(name="fresh", version="1.0")
        fields = emit.call_args.kwargs
        self.assertIsNone(fields["replaced_pkg_id"])
        self.assertEqual(fields["cascaded_files"], 0)
        self.assertEqual(fields["cascaded_depends"], 0)

    # -- the config_files asymmetry --------------------------------------

    def test_config_baseline_survives_the_replace_and_relinks(self):
        """ON DELETE SET NULL is what preserves a baseline across a reinstall.

        The row is orphaned to package_id NULL by the replace, keeps its
        first-install checksum, and add_files re-links it to the new id via its
        ON CONFLICT(path) clause — which is why a plain reinstall does not
        re-baseline a user-edited /etc file.
        """
        conf = "etc/demo/demo.conf"
        old_id = self._register(files=("usr/bin/demo", conf))
        baseline = self.db.conn.execute(
            "SELECT original_checksum FROM config_files WHERE path = ?",
            (conf,)).fetchone()[0]
        self.assertEqual(baseline, "a" * 64)

        new_id = self.db.add_installed(name="demo", version="2.0",
                                       replace_existing=True)
        row = self.db.conn.execute(
            "SELECT package_id, original_checksum FROM config_files "
            "WHERE path = ?", (conf,)).fetchone()
        self.assertIsNone(row[0], "the FK is SET NULL, so the row is orphaned")
        self.assertEqual(row[1], baseline, "the baseline itself is preserved")

        self.db.add_files(new_id, ["usr/bin/demo", conf],
                          hashes={"usr/bin/demo": "b" * 64, conf: "b" * 64})
        row = self.db.conn.execute(
            "SELECT package_id, original_checksum FROM config_files "
            "WHERE path = ?", (conf,)).fetchone()
        self.assertEqual(row[0], new_id, "re-adding files re-links the baseline")
        self.assertEqual(row[1], baseline,
                         "and must NOT ratchet it to the new stock checksum")

    # -- the production re-register paths --------------------------------

    def test_import_manifests_reregister_is_declared(self):
        """`pkm import` is the majority path — core, base and core-extra all

        register only by shelling out to it. If the declaration were not
        threaded there, every bash-tier package's re-registration would raise.
        """
        rel = "usr/bin/demo"
        live = self.root / rel
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text("payload\n")

        pkg_id = self.db.add_installed(name="demo", version="1.0", release=2,
                                       tier="core", install_method="archive")
        self.db.add_files(pkg_id, [rel], hashes={rel: "a" * 64})
        self.db.add_depends(pkg_id, [("libc", "runtime")])

        mdir = Path(self.tmp.name) / "manifests"
        mdir.mkdir()
        (mdir / "demo-1.0").write_text(
            "PACKAGE NAME: demo-1.0\n"
            "PACKAGE VERSION: 1.0\n"
            "PACKAGE RELEASE: 2\n"
            "UNCOMPRESSED SIZE: 1K (1024 bytes)\n"
            "BUILD DATE: 2026-07-30T00:00:00Z\n"
            "DESCRIPTION:\n"
            "demo: a package\n"
            "\n"
            "FILE LIST:\n"
            f"{rel}\n"
        )
        self.assertEqual(self.db.import_manifests(mdir), 1)

        row = self.db.get_installed("demo")
        self.assertEqual(row["tier"], "core")
        new_files = self.db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE package_id = ?",
            (row["id"],)).fetchone()[0]
        self.assertEqual(new_files, 1,
                         "the re-register must leave the payload registered")
        self.assertEqual({d["name"] for d in self.db.get_depends("demo")},
                         {"libc"})


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The installer's post-hook checksum reconcile is SCOPED to the install set.

PHASE_HOOKS sources every installed recipe's post_install() inside a chroot of
the target, and the reconcile runs immediately afterwards. Its job is narrow:
signing and hooks legitimately rewrite files after pkm recorded their archive
hashes (the MOK-signed kernel and UKI, hook-edited desktop entries and XML
catalogs), so the recorded hash has to catch up or `pkm verify` false-flags a
correct install.

Run unscoped, it does more than that. reconcile_checksums_from_live(paths=None)
re-records EVERY non-config file row on the target from disk, so whatever is on
disk at that moment becomes the recorded truth — including a path a hook wrote
over that belongs to a different package. PackageDB's own docstring reserves
the unscoped form for a tree that is known-good in whole, and pkm's installer
already passes the scoped form twelve lines of code away.

Scoping to the union of the installed packages' owned paths keeps every
legitimate case (signing and hooks both land on installed packages' own files)
and leaves anything outside the install set to be judged by verify on its
merits, which is the entire point of having a recorded hash.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from installer.backend import packages  # noqa: E402
from pkm.database import PackageDB, _sha256  # noqa: E402


class ReconcileScopeTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.target = Path(self._td.name) / "target"
        (self.target / "var" / "lib" / "igos").mkdir(parents=True)
        self.db_path = self.target / "var" / "lib" / "igos" / "pkm.db"

    def tearDown(self):
        self._td.cleanup()

    def _write(self, rel, content):
        p = self.target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def _register(self, name, rels):
        db = PackageDB(str(self.db_path), root=str(self.target))
        try:
            pkg_id = db.add_installed(name=name, version="1.0", release=1,
                                      tier="core", install_method="archive")
            db.add_files(pkg_id, rels, hashes={
                r: _sha256(str(self.target / r)) for r in rels})
        finally:
            db.close()

    def _recorded(self, rel):
        db = PackageDB(str(self.db_path), root=str(self.target))
        try:
            row = db.conn.execute(
                "SELECT checksum FROM files WHERE path = ?", (rel,)).fetchone()
        finally:
            db.close()
        return row[0] if row else None

    def test_scoped_reconcile_updates_only_the_install_set(self):
        inside = "usr/bin/inside"
        outside = "usr/bin/outside"
        self._write(inside, b"original\n")
        self._write(outside, b"original\n")
        self._register("inside-pkg", [inside])
        self._register("outside-pkg", [outside])
        original_outside = self._recorded(outside)

        # A hook rewrites both: one file belongs to the install set, the
        # other does not.
        self._write(inside, b"rewritten by a hook\n")
        self._write(outside, b"rewritten by a hook\n")

        updated = packages.reconcile_checksums(
            self.target, installed_names=["inside-pkg"])

        self.assertGreaterEqual(updated, 1)
        self.assertEqual(self._recorded(inside),
                         _sha256(str(self.target / inside)))
        self.assertEqual(
            self._recorded(outside), original_outside,
            "a file outside the install set must keep its recorded hash so "
            "verify can still report the change")

    def test_an_empty_install_set_reconciles_nothing(self):
        rel = "usr/bin/thing"
        self._write(rel, b"original\n")
        self._register("thing", [rel])
        original = self._recorded(rel)
        self._write(rel, b"changed\n")

        self.assertEqual(
            packages.reconcile_checksums(self.target, installed_names=[]), 0)
        self.assertEqual(self._recorded(rel), original)

    def test_none_keeps_the_whole_tree_form_for_deliberate_callers(self):
        rel = "usr/bin/thing"
        self._write(rel, b"original\n")
        self._register("thing", [rel])
        self._write(rel, b"changed\n")

        self.assertGreaterEqual(
            packages.reconcile_checksums(self.target, installed_names=None), 1)
        self.assertEqual(self._recorded(rel), _sha256(str(self.target / rel)))

    def test_a_missing_database_is_a_no_op(self):
        self.db_path.unlink(missing_ok=True)
        self.assertEqual(
            packages.reconcile_checksums(self.target, installed_names=["x"]), 0)

    def test_the_installer_passes_the_resolved_install_set(self):
        """The call site is what makes the scoping real."""
        source = (REPO_ROOT / "installer" / "backend" / "install.py").read_text()
        self.assertIn("packages.reconcile_checksums(", source)
        idx = source.index("packages.reconcile_checksums(")
        call = source[idx:idx + 160]
        self.assertIn("installed_names=installed_names", call,
                      "the installer must scope the reconcile to the packages "
                      f"it actually installed; call reads: {call!r}")


if __name__ == "__main__":
    unittest.main()

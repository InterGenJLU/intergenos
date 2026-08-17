# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Component A — deploy-layer content-keyed re-register (pkm import).

`import_manifests` re-registers a package whenever the current text manifest's
sha256 differs from the value stored on its `installed` row. A prior fix caught
the VERSION-bump class; this closes the SAME-version, content-only class (a
release-bump rebuild — the standard iteration shape — or any content change),
the deploy-layer staleness that failed `pkm verify` on the shipped system after
a same-version rebuild (a prior build cycle's bash-tier class: kernel/python
recovered only by a manual DB-drop + reinstall). The honest criterion: the DB
row mirrors exactly the manifest bytes it was built from.

Acceptance criteria implemented here:
  A1 same-version changed-content manifest -> row refreshes, verify green, no manual recovery.
  A2 identical manifest -> true no-op, row id stable.
  A3 version-bump path still re-registers (regression alongside the existing reregister tests).
  A4 migration adds the column on a pre-column DB; a NULL-hash row re-registers once then no-ops.
  A5 legacy manifest (no PACKAGE RELEASE) parses/imports; extended manifest round-trips release.
"""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ComponentAContentReRegisterTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.mdir = self.tmp / "manifests"
        self.mdir.mkdir()
        self.dbpath = self.tmp / "pkm.db"
        self.db = PackageDB(self.dbpath, root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _write_pkg(self, name, version, file_contents, release=None,
                   build_date="2026-01-01T00:00:00Z"):
        """Lay down the on-disk files + the sole text manifest for <name>.

        file_contents: {relpath: content}. The manifest FILE LIST carries the
        RFC-v1 `<path> sha256:<hex>` suffix computed from the content, so a
        content change deterministically changes the manifest bytes (mirroring
        a real rebuild, without depending on the BUILD DATE timestamp). Replaces
        any prior-version manifest the way the build's stale-manifest sweep does.
        """
        for rel, content in file_contents.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        for stale in self.mdir.glob(f"{name}-*"):
            stale.unlink()
        lines = [f"PACKAGE NAME: {name}-{version}",
                 f"PACKAGE VERSION: {version}"]
        if release is not None:
            lines.append(f"PACKAGE RELEASE: {release}")
        lines += [f"BUILD DATE: {build_date}", "FILE LIST:"]
        for rel, content in sorted(file_contents.items()):
            lines.append(f"{rel} sha256:{_sha(content)}")
        (self.mdir / f"{name}-{version}").write_text("\n".join(lines) + "\n")

    # ---- A1 ---------------------------------------------------------------
    def test_a1_same_version_content_change_reregisters(self):
        self._write_pkg("foo", "1.0", {"usr/bin/foo": "payload-r1"})
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        v = self.db.verify_package("foo")
        self.assertEqual((v["missing"], v["modified"]), ([], []))

        # SAME version, rewritten content (a release-bump rebuild). On-disk +
        # manifest change; version does not.
        self._write_pkg("foo", "1.0", {"usr/bin/foo": "payload-r2-CHANGED"})
        self.assertEqual(self.db.import_manifests(self.mdir), 1,
                         "a same-version content change must re-register, not skip")
        v = self.db.verify_package("foo")
        self.assertEqual((v["missing"], v["modified"]), ([], []),
                         "recomputed checksums must match the new content — "
                         "verify green with zero manual recovery")

    # ---- A2 (rescoped per Adjudication 2026-07-14) -----------------------
    def test_a2_unrewritten_manifest_is_noop(self):
        # The no-op guarantee is SCOPED to a manifest NOT rewritten since the
        # last import (identical bytes on disk). Re-import -> no-op, id stable.
        self._write_pkg("bar", "1.0", {"usr/bin/bar": "x"})
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        id1 = self.db.get_installed("bar")["id"]
        h1 = self.db.get_installed("bar")["manifest_sha256"]
        self.assertIsNotNone(h1, "first import must store the manifest hash")
        self.assertEqual(self.db.import_manifests(self.mdir), 0)
        self.assertEqual(self.db.get_installed("bar")["id"], id1)

    def test_a2_rewritten_identical_content_reregisters(self):
        # Adjudicated 2026-07-14 (A-X2 churn horn): a REWRITTEN manifest —
        # even byte-identical content but a fresh BUILD DATE — is a redeploy, and
        # honestly re-proving the row from disk IS the intended firing condition,
        # NOT churn to be suppressed. pkg_manifest rewrites BUILD DATE on every
        # deploy, so manifest_sha256 changes exactly when the deploy layer
        # redeployed the package.
        self._write_pkg("baz2", "1.0", {"usr/bin/baz2": "same"},
                        build_date="2026-01-01T00:00:00Z")
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        # Same version, SAME file content, but a new BUILD DATE (a redeploy).
        self._write_pkg("baz2", "1.0", {"usr/bin/baz2": "same"},
                        build_date="2026-02-02T00:00:00Z")
        self.assertEqual(self.db.import_manifests(self.mdir), 1,
                         "a rewritten manifest (fresh BUILD DATE) must re-register "
                         "— the honest redeploy behavior, not churn")
        v = self.db.verify_package("baz2")
        self.assertEqual((v["missing"], v["modified"]), ([], []))

    def test_ax1_disk_mutation_without_manifest_rewrite_is_out_of_scope(self):
        # Adjudicated 2026-07-14 (A-X1 scope-boundary probe): disk bytes
        # change with NO manifest rewrite (the post-register hook-mutation class —
        # depmod indexes, perllocal). This is OUT of Component A's scope (A =
        # deploy-layer staleness, manifest rewritten but import skipped) and is
        # Component B's absence-vs-mutation lane. Correct outcome: A does NOT
        # fire, and the mutation SURFACES in verify — never a silent pass.
        self._write_pkg("hooky", "1.0", {"usr/bin/hooky": "original"})
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        id1 = self.db.get_installed("hooky")["id"]
        # Mutate the deployed file WITHOUT rewriting the manifest (a post-install
        # hook editing an already-registered file).
        (self.root / "usr/bin/hooky").write_text("mutated-by-a-hook")
        self.assertEqual(self.db.import_manifests(self.mdir), 0,
                         "Component A must NOT fire when the manifest was not rewritten")
        self.assertEqual(self.db.get_installed("hooky")["id"], id1,
                         "no re-register -> row id stable")
        v = self.db.verify_package("hooky")
        self.assertIn("usr/bin/hooky", v["modified"],
                      "the hook mutation must surface in verify, never a silent pass")

    # ---- A3 ---------------------------------------------------------------
    def test_a3_version_bump_still_reregisters(self):
        self._write_pkg("baz", "1.0", {"usr/bin/baz": "one"})
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        self._write_pkg("baz", "2.0", {"usr/bin/baz": "two"})
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        row = self.db.get_installed("baz")
        self.assertEqual(row["version"], "2.0")
        v = self.db.verify_package("baz")
        self.assertEqual((v["missing"], v["modified"]), ([], []))

    # ---- A4 ---------------------------------------------------------------
    def test_a4_migration_adds_column_and_null_backfills(self):
        # Build a DB whose `installed` table PREDATES the manifest_sha256 column
        # (the substrate-DB case: the burn reverts to a chroot pkm.db built
        # before the column). A raw pre-column table, then reopen via PackageDB.
        raw = self.tmp / "old.db"
        con = sqlite3.connect(raw)
        # Long-standing columns present in a genuine pre-manifest_sha256 pkm.db.
        # PackageDB's migrations auto-add install_reason / held / degraded /
        # superseded_* / manifest_sha256; the rest have always been there, so
        # the fixture carries them (a too-minimal table would fail add_installed
        # for reasons unrelated to this migration).
        con.executescript(
            """
            CREATE TABLE installed (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
                release INTEGER DEFAULT 1, tier TEXT, description TEXT,
                license TEXT, build_date TEXT, install_date TEXT,
                install_method TEXT, archive_path TEXT,
                uncompressed_size INTEGER, compressed_size INTEGER,
                UNIQUE(name)
            );
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                package_id INTEGER NOT NULL REFERENCES installed(id) ON DELETE CASCADE,
                path TEXT NOT NULL, is_dir BOOLEAN DEFAULT 0,
                is_config BOOLEAN DEFAULT 0, checksum TEXT
            );
            INSERT INTO installed (name, version) VALUES ('foo', '1.0');
            """
        )
        con.commit()
        cols_before = {r[1] for r in con.execute("PRAGMA table_info(installed)")}
        con.close()
        self.assertNotIn("manifest_sha256", cols_before,
                         "fixture must genuinely predate the column")

        old = PackageDB(raw, root=str(self.root))
        try:
            cols_after = {r[1] for r in
                          old.conn.execute("PRAGMA table_info(installed)")}
            self.assertIn("manifest_sha256", cols_after,
                          "migration must ADD the column to a pre-column DB")
            row = old.get_installed("foo")
            self.assertIsNone(row["manifest_sha256"],
                              "existing row's hash is NULL until backfilled")

            # A NULL stored hash is "provenance unproven" -> re-register once.
            self._write_pkg("foo", "1.0", {"usr/bin/foo": "content"})
            self.assertEqual(old.import_manifests(self.mdir), 1,
                             "NULL-hash row must re-register once to backfill")
            self.assertIsNotNone(old.get_installed("foo")["manifest_sha256"])
            # Second import over the now-hashed row: no-op.
            self.assertEqual(old.import_manifests(self.mdir), 0,
                             "backfilled row must no-op on the next import")
        finally:
            old.close()

    # ---- A5 ---------------------------------------------------------------
    def test_a5_legacy_no_release_and_extended_release_roundtrip(self):
        # Legacy manifest: no PACKAGE RELEASE header -> parses, imports, release
        # falls back to the schema default (1).
        self._write_pkg("leg", "1.0", {"usr/bin/leg": "l"}, release=None)
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        self.assertEqual(self.db.get_installed("leg")["release"], 1,
                         "legacy manifest without a release defaults to 1")

        # Extended manifest: PACKAGE RELEASE round-trips into installed.release.
        self._write_pkg("ext", "1.0", {"usr/bin/ext": "e"}, release=4)
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        self.assertEqual(self.db.get_installed("ext")["release"], 4,
                         "PACKAGE RELEASE must round-trip into the DB row")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The hook-managed content class must survive `pkm import`, including on a
database that has never seen the package.

D-9b gave the ARCHIVE INSTALL path the rule: a payload file rewritten in place
by the package's own sealed hook is reclassified to the hook-generated content
class, so verify existence-checks it instead of byte-comparing it against the
pre-hook archive hash. That reclassification was recorded only in the SQLite
row, and `pkm import` could recover it only by CARRYING it from a row that
already had it.

A source-built chroot has no such row. Its build order is
stage -> manifest (hashes the PRISTINE staging tree) -> archive -> deploy ->
post_install (rewrites the deployed file) -> `pkm import`, so the import
registers the pristine hash with the file unflagged, and the ISO metadata-sync
gate byte-compares a correct image and refuses it. Measured on a banked
from-scratch chroot database: 881,959 file rows, ZERO carrying the flag, with
docbook-xml's catalog.xml recorded at its pristine payload hash (9cf6672e…)
while the live chroot file was the post_install product.

The fix makes the text manifest — the artifact the import actually reads —
able to STATE the class, so the attribution survives a from-scratch import
instead of depending on a row that does not exist yet.

The refusals are as load-bearing as the attribution. A manifest may only
classify what it itself declares, and marking a path can never reach another
package's ownership row: an import must not be a way to move a file's
attribution from one package to another.
"""

from __future__ import annotations

import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


def _touch(path: Path, content: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class _Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "t.db", root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))
        self.manifest_dir = self.root / "var" / "lib" / "igos" / "packages"

    def tearDown(self):
        self.db.close()

    # -- fixtures ------------------------------------------------------

    def _archive(self, name="demo", version="1.0", hook_body=None,
                 payload=(("usr/share/demo/catalog", "pristine\n"),)):
        stg = self.tmp / f"stg-{name}-{version}"
        for rel, content in payload:
            _touch(stg / rel, content)
        (stg / ".PKGINFO").write_text(
            f"pkgname = {name}\npkgver = {version}\n")
        if hook_body is not None:
            scripts = stg / ".scripts"
            scripts.mkdir()
            hook = scripts / "post_install.sh"
            hook.write_text("#!/bin/bash\nset -e\n" + hook_body)
            hook.chmod(0o755)
        arc = self.tmp / f"{name}-{version}.igos.tar.gz"
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(stg, arcname=".")
        return arc

    def _write_manifest_file(self, name, version, files, hook_managed=(),
                             hashes=None):
        """Write a manifest by hand, in the on-disk text format.

        Deliberately hand-built rather than produced by the installer: these
        cases pin what the PARSER and the IMPORT must do with bytes they are
        handed, including bytes that claim something they must not be allowed
        to claim.
        """
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            f"PACKAGE NAME: {name}-{version}",
            f"PACKAGE VERSION: {version}",
            "PACKAGE RELEASE: 1",
            "UNCOMPRESSED SIZE: 1K (1024 bytes)",
            "BUILD DATE: 2026-08-06T00:00:00Z",
            "BUILD SYSTEM: InterGenOS pkm",
        ]
        lines += [f"HOOK-MANAGED: {p}" for p in hook_managed]
        lines += [
            "DESCRIPTION:",
            f"{name}: test package",
            "",
            "FILE LIST:",
        ]
        for f in files:
            h = (hashes or {}).get(f)
            lines.append(f"{f} sha256:{h}" if h else f)
        path = self.manifest_dir / f"{name}-{version}"
        path.write_text("\n".join(lines) + "\n")
        return path

    def _row(self, pkg, path):
        return self.db.conn.execute(
            "SELECT f.is_generated FROM files f JOIN installed i "
            "ON f.package_id = i.id WHERE i.name = ? AND f.path = ?",
            (pkg, path),
        ).fetchone()


class ImportAttributionTests(_Harness):
    """The class must survive an import that has no prior row to carry."""

    REWRITE_OWN = (
        'printf "rewritten by hook" > '
        '"$PKM_PACKAGE_ROOT/usr/share/demo/catalog"\n'
    )

    def test_manifest_states_hook_managed_and_import_attributes_it(self):
        _touch(self.root / "usr/share/demo/catalog", "rewritten by hook")
        self._write_manifest_file(
            "demo", "1.0",
            ["usr/", "usr/share/", "usr/share/demo/",
             "usr/share/demo/catalog"],
            hook_managed=["usr/share/demo/catalog"],
        )
        self.db.import_manifests(manifest_dir=str(self.manifest_dir))
        row = self._row("demo", "usr/share/demo/catalog")
        self.assertIsNotNone(row, "the import registered no row for the path")
        self.assertEqual(
            row[0], 1,
            "a manifest that STATES the path is hook-managed must import it "
            "into the hook-generated content class; a database that has "
            "never seen the package has no row to carry the flag from, which "
            "is exactly the from-scratch chroot case the ISO metadata-sync "
            "gate refuses")

    def test_deploy_then_import_into_a_fresh_database_keeps_the_class(self):
        """The end-to-end shape: install, then import with nothing to carry."""
        ok, msg = self.installer.install(
            "demo",
            archive_path=str(self._archive(hook_body=self.REWRITE_OWN)))
        self.assertTrue(ok, msg)
        self.assertEqual(
            self._row("demo", "usr/share/demo/catalog")[0], 1,
            "precondition: the deploy path's D-9b rule must flip the row")

        fresh = PackageDB(self.tmp / "fresh.db", root=str(self.root))
        try:
            fresh.import_manifests(manifest_dir=str(self.manifest_dir))
            row = fresh.conn.execute(
                "SELECT f.is_generated FROM files f JOIN installed i "
                "ON f.package_id = i.id "
                "WHERE i.name = 'demo' AND f.path = ?",
                ("usr/share/demo/catalog",),
            ).fetchone()
            self.assertIsNotNone(row, "the import registered no row")
            self.assertEqual(
                row[0], 1,
                "the manifest the install wrote must carry the class forward "
                "into a database that never saw the package — otherwise "
                "every source-built chroot registers the file unflagged and "
                "the gate byte-compares it against the pre-hook bytes")
        finally:
            fresh.close()

    def test_verify_is_clean_after_a_fresh_import(self):
        _touch(self.root / "usr/share/demo/catalog", "rewritten by hook")
        self._write_manifest_file(
            "demo", "1.0",
            ["usr/share/demo/catalog"],
            hook_managed=["usr/share/demo/catalog"],
            # The manifest states the PRISTINE hash, as a manifest written
            # from the staging tree before the hook ran always will.
            hashes={"usr/share/demo/catalog": "0" * 64},
        )
        self.db.import_manifests(manifest_dir=str(self.manifest_dir))
        result = self.db.verify_package("demo")
        self.assertEqual(
            result["modified"], [],
            "an imported hook-managed file must be existence-checked, not "
            "byte-compared against the pre-hook hash the manifest carries")
        self.assertIn(
            "usr/share/demo/catalog",
            [p.lstrip("/") for p in result.get("generated", [])],
            "it belongs in the named generated bucket, never silently "
            "skipped")


class ImportLaunderingRefusalTests(_Harness):
    """An import must never move a file's attribution to another package."""

    def test_manifest_cannot_classify_a_path_it_does_not_declare(self):
        _touch(self.root / "usr/share/other/data", "bytes\n")
        self._write_manifest_file(
            "demo", "1.0",
            ["usr/share/demo/own"],
            hook_managed=["usr/share/other/data"],
        )
        _touch(self.root / "usr/share/demo/own", "own\n")
        self.db.import_manifests(manifest_dir=str(self.manifest_dir))

        self.assertIsNone(
            self._row("demo", "usr/share/other/data"),
            "a path the manifest does not declare in its own FILE LIST must "
            "not gain an ownership row through the hook-managed header")
        refusals = " ".join(getattr(self.db, "import_refusals", []))
        self.assertIn(
            "usr/share/other/data", refusals,
            "the refusal must be REPORTED, not silently dropped — a manifest "
            "claiming a path it does not own is exactly the condition an "
            "operator needs to see")
        self.assertIn("demo", refusals,
                      "the refusal must name the claiming package")

    def test_manifest_cannot_flip_another_packages_row(self):
        _touch(self.root / "usr/share/victim/data", "victim bytes\n")
        self._write_manifest_file(
            "victim", "1.0", ["usr/share/victim/data"])
        self.db.import_manifests(manifest_dir=str(self.manifest_dir))
        self.assertIsNotNone(
            self._row("victim", "usr/share/victim/data"),
            "precondition: the victim package must own the path")

        # A second manifest declares the SAME path in its own file list and
        # claims it is hook-managed. Declaring it is enough to gain a row of
        # its own — that is pre-existing ownership behaviour — but it must
        # never reach the row the victim already holds.
        self._write_manifest_file(
            "greedy", "1.0",
            ["usr/share/victim/data"],
            hook_managed=["usr/share/victim/data"],
        )
        self.db.import_manifests(manifest_dir=str(self.manifest_dir))

        victim_row = self._row("victim", "usr/share/victim/data")
        self.assertIsNotNone(victim_row)
        self.assertEqual(
            victim_row[0], 0,
            "one package's manifest must never downgrade ANOTHER package's "
            "row to existence-only — that would let an import launder a "
            "tampered file into a classification that stops checking it")
        result = self.db.verify_package("victim")
        self.root.joinpath("usr/share/victim/data").write_text("tampered\n")
        result = self.db.verify_package("victim")
        self.assertEqual(
            [p.lstrip("/") for p in result["modified"]],
            ["usr/share/victim/data"],
            "the victim's byte check must stay exactly as strict")


class ManifestFormatCompatibilityTests(_Harness):
    """The header is additive: manifests without it must parse unchanged."""

    def test_legacy_manifest_without_the_header_imports_unflagged(self):
        _touch(self.root / "usr/share/demo/plain", "bytes\n")
        self._write_manifest_file(
            "demo", "1.0", ["usr/share/demo/plain"])
        self.db.import_manifests(manifest_dir=str(self.manifest_dir))
        row = self._row("demo", "usr/share/demo/plain")
        self.assertIsNotNone(row)
        self.assertEqual(
            row[0], 0,
            "a manifest that states nothing must classify nothing — silence "
            "is not a claim")
        self.assertEqual(
            list(getattr(self.db, "import_refusals", [])), [],
            "a legacy manifest is not a refusal")

    def test_carried_flag_still_survives_a_re_register(self):
        """The pre-existing carry rule must keep working alongside the header."""
        _touch(self.root / "usr/share/demo/catalog", "content\n")
        self._write_manifest_file(
            "demo", "1.0", ["usr/share/demo/catalog"])
        self.db.import_manifests(manifest_dir=str(self.manifest_dir))
        pkg = self.db.get_installed("demo")
        self.db.mark_files_generated(pkg["id"], ["usr/share/demo/catalog"])

        # Rewrite the manifest bytes so the content-keyed re-register fires.
        self._write_manifest_file(
            "demo", "1.0",
            ["usr/share/demo/catalog", "usr/share/demo/extra"])
        _touch(self.root / "usr/share/demo/extra", "extra\n")
        self.db.import_manifests(manifest_dir=str(self.manifest_dir))

        self.assertEqual(
            self._row("demo", "usr/share/demo/catalog")[0], 1,
            "the row keeps what it already knows when the manifest states "
            "nothing about it")


if __name__ == "__main__":
    unittest.main()

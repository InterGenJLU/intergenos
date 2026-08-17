# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""D-9b: a package's own payload file rewritten by its own sealed hook is
reclassified to the hook-generated content class.

The class's first live member: docbook-xml installs the pristine upstream
catalog.xml and its sealed post_install rewrites it in place with
xmlcatalog. The files row keeps the pre-hook archive checksum, so without
the reclassification `pkm verify` reports designed behavior as damage on
every installed system, forever, and the ISO metadata-sync gate refuses a
correct image (first fired on the ge9b-13 mint, 2026-08-05).

The rule is deliberately narrow: only paths the SAME package's payload owns
flip. A hook that modifies another package's file keeps hookrecord's
original treatment verbatim — reported, never absorbed.
"""

from __future__ import annotations

import os
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

    def tearDown(self):
        self.db.close()

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

    def _row(self, path):
        return self.db.conn.execute(
            "SELECT is_generated FROM files WHERE path = ?", (path,),
        ).fetchone()


class OwnPayloadHookModifiedTests(_Harness):

    REWRITE_OWN = (
        'printf "rewritten by hook" > '
        '"$PKM_PACKAGE_ROOT/usr/share/demo/catalog"\n'
    )

    def test_own_payload_file_rewritten_by_hook_flips_to_generated(self):
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=self.REWRITE_OWN)))
        self.assertTrue(ok, msg)
        row = self._row("usr/share/demo/catalog")
        self.assertIsNotNone(row)
        self.assertEqual(
            row[0], 1,
            "the hook rewrote the package's own payload file; the row must "
            "carry the generated content class or verify reports designed "
            "behavior as damage forever")

    def test_verify_is_clean_after_the_hook_rewrite(self):
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=self.REWRITE_OWN)))
        self.assertTrue(ok, msg)
        result = self.db.verify_package("demo")
        self.assertEqual(
            result["modified"], [],
            "hook-managed content must not be reported as modified")
        self.assertIn("usr/share/demo/catalog",
                      [p.lstrip("/") for p in result.get("generated", [])],
                      "the file belongs in the named generated bucket, "
                      "never silently skipped")

    def test_unhooked_payload_still_reports_modified(self):
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=None)))
        self.assertTrue(ok, msg)
        (self.root / "usr/share/demo/catalog").write_text("tampered\n")
        result = self.db.verify_package("demo")
        self.assertEqual(
            [p.lstrip("/") for p in result["modified"]],
            ["usr/share/demo/catalog"],
            "without a hook the byte check must stay exactly as strict")

    def test_another_packages_file_modified_by_hook_is_not_flipped(self):
        ok, msg = self.installer.install(
            "victim", archive_path=str(self._archive(
                name="victim",
                payload=(("usr/share/victim/data", "victim bytes\n"),))))
        self.assertTrue(ok, msg)
        hook = (
            'printf "overwritten" > '
            '"$PKM_PACKAGE_ROOT/usr/share/victim/data"\n'
        )
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=hook)))
        self.assertTrue(ok, msg)
        row = self._row("usr/share/victim/data")
        self.assertIsNotNone(row)
        self.assertEqual(
            row[0], 0,
            "a hook touching ANOTHER package's file must never launder the "
            "damage into a hook-managed classification — the victim's "
            "verify must keep reporting it")
        result = self.db.verify_package("victim")
        self.assertEqual(
            [p.lstrip("/") for p in result["modified"]],
            ["usr/share/victim/data"])


if __name__ == "__main__":
    unittest.main()

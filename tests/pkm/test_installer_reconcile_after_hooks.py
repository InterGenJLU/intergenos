# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Post-install checksum reconcile wiring (PKM-E).

PackageDB.reconcile_checksums_from_live existed with a docstring
claiming "Called by the installer AFTER post-install hooks + signing
run" — but nothing in production called it, so a file a hook rewrites
after deploy kept its staged-time archive hash in the DB and
`pkm verify --strict` over-reported it as modified.

Demonstrated end-to-end: install an archive whose own
.scripts/post_install.sh lifecycle hook mutates a deployed payload file;
the DB checksum for that file must equal the sha256 of the MUTATED live
bytes, not the archive bytes.

Also pins the scoping contract: a reconcile scoped via paths= must not
touch rows outside the given set (an unscoped reconcile re-blesses the
whole live tree, which would launder unrelated drift into the DB).
"""
from __future__ import annotations

import hashlib
import tarfile
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB, _sha256
from pkm.installer import PackageInstaller


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReconcileAfterHooksTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "t.db", root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))

    def tearDown(self):
        self.db.close()

    def _archive_with_mutating_hook(self, name="demo", version="1.0"):
        stg = self.tmp / f"stg-{name}"
        payload = stg / "usr" / "share" / name / "data.txt"
        payload.parent.mkdir(parents=True)
        payload.write_text("original archive content\n")
        (stg / ".PKGINFO").write_text(
            f"pkgname = {name}\npkgver = {version}\n")
        scripts = stg / ".scripts"
        scripts.mkdir()
        hook = scripts / "post_install.sh"
        hook.write_text(
            "#!/bin/bash\n"
            f'echo "mutated by post_install hook" > '
            f'"$PKM_PACKAGE_ROOT/usr/share/{name}/data.txt"\n'
        )
        hook.chmod(0o755)
        arc = self.tmp / f"{name}-{version}.igos.tar.gz"
        # Real archiver form: members are `./`-prefixed (tar -C stg .).
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(stg, arcname=".")
        return arc

    def test_db_checksum_reflects_post_hook_bytes(self):
        arc = self._archive_with_mutating_hook()
        ok, msg = self.installer.install("demo", archive_path=str(arc))
        self.assertTrue(ok, msg)

        live = self.root / "usr" / "share" / "demo" / "data.txt"
        live_bytes = live.read_bytes()
        self.assertEqual(live_bytes, b"mutated by post_install hook\n")

        row = self.db.conn.execute(
            "SELECT checksum FROM files WHERE path = ? AND is_dir = 0",
            ("usr/share/demo/data.txt",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(
            row[0], _sha(live_bytes),
            "DB checksum must be the post-hook live hash, not the "
            "archive-staged hash",
        )


class ReconcileScopingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        (self.root / "usr" / "bin").mkdir(parents=True)
        self.db = PackageDB(self.tmp / "t.db", root=str(self.root))

    def tearDown(self):
        self.db.close()

    def _register(self, name, relpath, content):
        p = self.root / relpath
        p.write_bytes(content)
        pkg_id = self.db.add_installed(name, "1.0", release=1, tier="core")
        self.db.add_files(pkg_id, [relpath], hashes={relpath: _sha256(str(p))})
        return p

    def test_scoped_reconcile_leaves_other_rows_untouched(self):
        pa = self._register("alpha", "usr/bin/alpha", b"alpha-v1")
        pb = self._register("beta", "usr/bin/beta", b"beta-v1")
        # Both live files drift after registration.
        pa.write_bytes(b"alpha-v2")
        pb.write_bytes(b"beta-v2")

        updated = self.db.reconcile_checksums_from_live(
            paths=["usr/bin/alpha"])
        self.assertEqual(updated, 1)

        rows = dict(self.db.conn.execute(
            "SELECT path, checksum FROM files WHERE is_dir = 0"))
        self.assertEqual(rows["usr/bin/alpha"], _sha(b"alpha-v2"))
        # beta's row must still hold the registration-time hash — the
        # scoped call must not re-bless drift outside its path set.
        self.assertEqual(rows["usr/bin/beta"], _sha(b"beta-v1"))

    def test_trailing_slash_and_dir_entries_are_ignored(self):
        pa = self._register("gamma", "usr/bin/gamma", b"gamma-v1")
        pa.write_bytes(b"gamma-v2")
        updated = self.db.reconcile_checksums_from_live(
            paths=["usr/bin/gamma", "usr/share/gamma/"])
        self.assertEqual(updated, 1)


if __name__ == "__main__":
    unittest.main()

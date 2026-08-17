# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for the reachability check in scripts/check-squashfs-ownership.py.

The flagship icon theme shipped its root directory 0770 root:root on two
candidates — unreadable by any non-root session — because the gate audited
ownership and never modes. Under the user-facing data trees every directory
must be o+rx and every file o+r; symlinks are exempt; the allowlist covers
reasoned exceptions; trees outside the user-facing set are not mode-checked.
"""

import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "squashfs_ownership_gate_test",
        REPO_ROOT / "scripts" / "check-squashfs-ownership.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestReachability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.chroot = base / "chroot"
        self.chroot.mkdir()
        self.db_path = base / "pkm.db"
        self.allowlist = base / "allowlist.txt"
        self.allowlist.write_text("# empty allowlist\n")
        self._db_rows = []
        self.mod = _load()

    def tearDown(self):
        self._tmp.cleanup()

    def _own(self, name, *paths):
        """Register paths as owned by an installed package."""
        self._db_rows.append((name, paths))

    def _write_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE installed (name TEXT, version TEXT)")
        conn.execute("CREATE TABLE files (path TEXT)")
        for name, paths in self._db_rows:
            conn.execute("INSERT INTO installed VALUES (?, '1.0')", (name,))
            for p in paths:
                conn.execute("INSERT INTO files VALUES (?)", (p,))
        conn.commit()
        conn.close()

    def _run(self):
        self._write_db()
        argv = ["check-squashfs-ownership", "--chroot", str(self.chroot),
                "--db", str(self.db_path),
                "--allowlist", str(self.allowlist)]
        out = io.StringIO()
        old = sys.argv
        sys.argv = argv
        try:
            with redirect_stdout(out):
                rc = self.mod.main()
        finally:
            sys.argv = old
        return rc, out.getvalue()

    def _ship(self, rel, content=b"x", dir_mode=None, file_mode=None):
        dest = self.chroot / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        if file_mode is not None:
            os.chmod(dest, file_mode)
        if dir_mode is not None:
            os.chmod(dest.parent, dir_mode)
        # Own every ancestor + the file so ownership never interferes
        # with what these tests measure.
        parts = Path(rel).parts
        owned = ["/".join(parts[:i]) for i in range(1, len(parts))] + [rel]
        self._own("fixture", *owned)

    def test_unreachable_icon_theme_dir_fails(self):
        self._ship("usr/share/icons/Theme - Blue/index.theme",
                   dir_mode=0o770)
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("user-facing path not world-reachable", out)
        self.assertIn("Theme - Blue", out)
        self.assertIn("0o770", out)

    def test_unreadable_file_in_user_facing_tree_fails(self):
        self._ship("usr/share/applications/app.desktop", file_mode=0o600)
        rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("user-facing path not world-reachable", out)
        self.assertIn("app.desktop", out)

    def test_world_reachable_tree_passes(self):
        self._ship("usr/share/icons/Good/index.theme",
                   dir_mode=0o755, file_mode=0o644)
        rc, out = self._run()
        self.assertEqual(rc, 0, out)

    def test_restrictive_mode_outside_user_facing_trees_is_not_flagged(self):
        self._ship("etc/private/secret.conf", dir_mode=0o700, file_mode=0o600)
        rc, out = self._run()
        self.assertEqual(rc, 0, out)

    def test_allowlist_covers_reasoned_exception(self):
        self._ship("usr/share/fonts/vendor/restricted.ttf", file_mode=0o640)
        self.allowlist.write_text(
            "usr/share/fonts/vendor/restricted.ttf  "
            "# reason: vendor license requires restricted read\n")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()

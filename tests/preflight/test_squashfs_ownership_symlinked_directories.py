# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/check-squashfs-ownership.py judges a symbolic link to a directory.

os.walk lists a live link to a directory under `dirs` and never descends it,
so the gate's per-file loop never met one: 313 such links shipped on the
R001.3 substrate with no row and no verdict, and only the dangling /opt/jdk
(its target pruned, so listed under `files`) surfaced, at halt #2 of the
R001.3 mint, 2026-10-16. A link is a file-class entry whatever it points to.

What is pinned: an unowned live link to a directory is a violation of its own
class; a row of the link's own path, or an allowlist entry, clears it; the
top-level merged-usr skeleton links are not judged; a dangling link keeps the
verdict it always had; the summary names how many such links were judged.
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
        "squashfs_ownership_gate_symlinked_dirs_test",
        REPO_ROOT / "scripts" / "check-squashfs-ownership.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSymlinkedDirectories(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.chroot = base / "chroot"
        self.chroot.mkdir()
        self.db_path = base / "pkm.db"
        self.allowlist = base / "allowlist.txt"
        self.allowlist.write_text("# empty allowlist\n")
        self._rows = []
        self.mod = _load()

    def tearDown(self):
        self._tmp.cleanup()

    def _own(self, *paths):
        self._rows.extend(paths)

    def _run(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE installed (name TEXT, version TEXT)")
        conn.execute("CREATE TABLE files (path TEXT)")
        conn.execute("INSERT INTO installed VALUES ('theme', '1.0')")
        for p in self._rows:
            conn.execute("INSERT INTO files VALUES (?)", (p,))
        conn.commit()
        conn.close()
        argv = ["check-squashfs-ownership", "--chroot", str(self.chroot),
                "--db", str(self.db_path), "--allowlist", str(self.allowlist)]
        out = io.StringIO()
        old = sys.argv
        sys.argv = argv
        try:
            with redirect_stdout(out):
                rc = self.mod.main()
        finally:
            sys.argv = old
        return rc, out.getvalue()

    def _theme(self):
        """usr/share/icons/Tela/16 (owned, with one owned file) and the link
        usr/share/icons/Tela/16@2x -> 16, the shape the icon themes ship."""
        d = self.chroot / "usr/share/icons/Tela/16"
        d.mkdir(parents=True)
        (d / "icon.svg").write_bytes(b"<svg/>")
        os.symlink("16", self.chroot / "usr/share/icons/Tela/16@2x")
        self._own("usr", "usr/share", "usr/share/icons", "usr/share/icons/Tela",
                  "usr/share/icons/Tela/16", "usr/share/icons/Tela/16/icon.svg")

    def test_an_unowned_live_link_to_a_directory_is_a_violation(self):
        self._theme()
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("== unowned symlink (to a directory): 1 ==", out)
        self.assertIn("/usr/share/icons/Tela/16@2x", out)
        self.assertIn("1 symbolic links to directories", out)

    def test_a_row_of_the_links_own_path_clears_it(self):
        self._theme()
        self._own("usr/share/icons/Tela/16@2x")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)

    def test_an_allowlist_entry_clears_it(self):
        self._theme()
        self.allowlist.write_text(
            "usr/share/icons/Tela/16@2x    the theme's scale alias\n")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)

    def test_the_top_level_skeleton_links_are_not_judged(self):
        (self.chroot / "usr/bin").mkdir(parents=True)
        (self.chroot / "usr/lib").mkdir()
        os.symlink("usr/bin", self.chroot / "bin")
        os.symlink("usr/lib", self.chroot / "lib")
        os.symlink("usr/lib", self.chroot / "lib64")
        self._own("usr", "usr/bin", "usr/lib")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("0 symbolic links to directories", out)

    def test_a_dangling_link_keeps_its_verdict(self):
        (self.chroot / "opt").mkdir()
        os.symlink("jdk-24", self.chroot / "opt/jdk")
        self._own("opt")
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn("== unowned symlink: 1 ==", out)
        self.assertIn("/opt/jdk", out)
        self.assertNotIn("(to a directory)", out)


if __name__ == "__main__":
    unittest.main()

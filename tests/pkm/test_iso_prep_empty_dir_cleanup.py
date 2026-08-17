#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm iso-prep` removes the directory skeletons its prune empties.

Removing a package's files leaves its parent chains behind. remove() sweeps
each package's own ancestors, but a chain usually only becomes empty once a
LATER package in the removal order is gone — and by then that package's sweep
has already run, while the later package's sweep only covers its own
ancestors. So the residue survives the whole prune and ships: a pruned
package's directory tree reads as present payload on a live evaluation
(a namespace-package import finds an empty site-packages tree and succeeds),
and 51 such chains needed manual disposition at the shipping-tree ownership
gate on the last candidate.

The post-pass tested here applies that same disposition inside the prune,
under the same rules the gate uses: rmdir only, only a directory that is
empty AND recorded by no remaining installed package under either is_dir
flag. Emptiness is the hard floor — anything a live payload still uses is
non-empty and therefore untouchable regardless of what any manifest says.

Two subtrees are exempt. Hook products (the /opt/<vendor> class) are written
after a manifest is sealed, so no manifest records them and whether they
should die with their package is a design question about hook-output
ownership that pkm state cannot answer; the sweep leaves them and keeps
walking. The package system's own state trees are not package-owned content
either — an empty one is a directory awaiting content, not residue.
"""
from __future__ import annotations

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pkm.cli import cmd_iso_prep
from pkm.database import PackageDB
from pkm.remover import ancestor_chain, prune_empty_unowned_dirs


class AncestorChainTests(unittest.TestCase):

    def test_chain_includes_the_path_and_stops_above_top_level(self):
        self.assertEqual(
            ancestor_chain("usr/lib/python3.14/site-packages/demo/mod.py"),
            {"usr/lib",
             "usr/lib/python3.14",
             "usr/lib/python3.14/site-packages",
             "usr/lib/python3.14/site-packages/demo",
             "usr/lib/python3.14/site-packages/demo/mod.py"},
        )

    def test_top_level_entry_is_never_a_candidate(self):
        """A single-segment path is FHS skeleton no package owns; removing

        one breaks the merged-usr compat symlinks the system resolves
        through, which a prune has already done once.
        """
        self.assertEqual(ancestor_chain("lib64"), set())
        self.assertEqual(ancestor_chain("usr/bin"), {"usr/bin"})


class _SweepFixture(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _live_file(self, rel, content="payload\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def _live_dir(self, rel):
        p = self.root / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _register(self, name, paths, version="1.0", **kw):
        pkg_id = self.db.add_installed(name=name, version=version, **kw)
        self.db.add_files(pkg_id, list(paths),
                          hashes={p: "a" * 64 for p in paths})
        return pkg_id

    def _exists(self, rel):
        return (self.root / rel).exists()


class PruneEmptyUnownedDirsTests(_SweepFixture):

    def test_emptied_chain_is_removed(self):
        rel = "usr/lib/python3.14/site-packages/demo/mod.py"
        self._live_file(rel)
        os.remove(self.root / rel)  # the prune already unlinked the payload
        removed, exempt = prune_empty_unowned_dirs(
            self.db, self.root, ancestor_chain(rel))

        self.assertEqual(exempt, [])
        self.assertEqual(
            removed,
            ["usr/lib/python3.14/site-packages/demo",
             "usr/lib/python3.14/site-packages",
             "usr/lib/python3.14",
             "usr/lib"],
            "the chain is removed deepest-first, each parent judged after "
            "its child is gone",
        )
        self.assertFalse(self._exists("usr/lib"))
        self.assertTrue(self._exists(""), "the root itself is untouched")

    def test_directory_recorded_by_a_survivor_is_kept(self):
        """The /etc/sysconfig class: a still-installed package records the

        directory, so it is not residue even when momentarily empty.
        """
        self._live_dir("etc/sysconfig")
        self._register("base-files", ["etc/sysconfig/"])
        removed, _ = prune_empty_unowned_dirs(
            self.db, self.root, ancestor_chain("etc/sysconfig/network"))
        self.assertEqual(removed, [])
        self.assertTrue(self._exists("etc/sysconfig"))

    def test_directory_recorded_with_a_wrong_is_dir_flag_is_kept(self):
        """The recording check is flag-agnostic on purpose.

        Registration paths have written directory rows with is_dir=0 at
        corpus scale; a check that trusted the flag would be blinded by
        exactly the bad data that produces skeletons in the first place.
        """
        self._live_dir("usr/share/shared")
        pkg_id = self.db.add_installed(name="keeper", version="1.0")
        # No trailing slash -> the row lands with is_dir = 0 for a real dir.
        self.db.add_files(pkg_id, ["usr/share/shared"], hashes={})
        flag = self.db.conn.execute(
            "SELECT is_dir FROM files WHERE path = 'usr/share/shared'"
        ).fetchone()[0]
        self.assertEqual(flag, 0, "fixture premise: the flag is wrong")

        removed, _ = prune_empty_unowned_dirs(
            self.db, self.root, ancestor_chain("usr/share/shared/gone.dat"))
        self.assertEqual(removed, [])
        self.assertTrue(self._exists("usr/share/shared"))

    def test_directory_with_a_surviving_file_is_kept(self):
        """Emptiness is the safety floor, independent of any record.

        The leftover here is recorded by nobody — it is exactly the case a
        manifest check alone would miss.
        """
        self._live_file("usr/lib/mixed/left-behind.so")
        removed, _ = prune_empty_unowned_dirs(
            self.db, self.root, ancestor_chain("usr/lib/mixed/gone.so"))
        self.assertEqual(removed, [])
        self.assertTrue(self._exists("usr/lib/mixed/left-behind.so"))

    def test_hook_product_subtree_is_left_alone_and_the_walk_continues(self):
        self._live_dir("opt/vendor/jdk")
        self._live_file("usr/lib/vendor/lib.so")
        os.remove(self.root / "usr/lib/vendor/lib.so")

        candidates = (ancestor_chain("opt/vendor/jdk/bin/java")
                      | ancestor_chain("usr/lib/vendor/lib.so"))
        removed, exempt = prune_empty_unowned_dirs(
            self.db, self.root, candidates)

        self.assertTrue(self._exists("opt/vendor/jdk"),
                        "a hook-product directory is never removed here")
        self.assertIn("opt/vendor/jdk", exempt,
                      "and it is reported, not silently skipped")
        self.assertIn("usr/lib/vendor", removed,
                      "the walk continues past the exemption")

    def test_package_state_tree_is_left_alone(self):
        self._live_dir("var/lib/igos/archives")
        removed, exempt = prune_empty_unowned_dirs(
            self.db, self.root,
            ancestor_chain("var/lib/igos/archives/x-1.0.igos.tar.gz"))
        self.assertEqual(removed, [])
        self.assertIn("var/lib/igos/archives", exempt)
        self.assertTrue(self._exists("var/lib/igos/archives"))

    def test_second_run_removes_nothing(self):
        rel = "usr/lib/python3.14/site-packages/demo/mod.py"
        self._live_file(rel)
        os.remove(self.root / rel)
        first, _ = prune_empty_unowned_dirs(
            self.db, self.root, ancestor_chain(rel))
        self.assertTrue(first)
        second, exempt = prune_empty_unowned_dirs(
            self.db, self.root, ancestor_chain(rel))
        self.assertEqual(second, [], "idempotent: nothing is left to remove")
        self.assertEqual(exempt, [])

    def test_symlinked_directory_is_never_rmdird(self):
        self._live_dir("usr/lib/real")
        os.symlink(str(self.root / "usr/lib/real"),
                   str(self.root / "usr/lib/link"))
        removed, _ = prune_empty_unowned_dirs(
            self.db, self.root, {"usr/lib/link", "usr/lib/real"})
        self.assertEqual(removed, ["usr/lib/real"])
        self.assertTrue(os.path.islink(str(self.root / "usr/lib/link")))


class IsoPrepCleanupWiringTests(_SweepFixture):
    """The measured residue shape, driven through the command itself.

    The per-package sweep inside remove() already clears a chain whose only
    contents were recorded paths. What it cannot clear is a chain held open
    by an UNRECORDED empty directory: the pruned package's own sweep sees a
    non-empty parent and declines, correctly, and no later package's sweep
    covers that chain. Measured on this exact two-package fixture against
    the unmodified code: an empty __pycache__ left the pruned package's
    directory, its site-packages parent and the whole interpreter path
    standing after both packages were removed.
    """

    def _list_file(self, *names):
        f = self.tmp / "iso-list.txt"
        f.write_text("\n".join(names) + "\n")
        return str(f)

    def test_prune_clears_a_chain_an_unrecorded_empty_dir_held_open(self):
        a = "usr/lib/python3.14/site-packages/pkg_a/mod.py"
        b = "usr/lib/python3.14/site-packages/pkg_b/mod.py"
        self._live_file(a)
        self._live_file(b)
        self._register("mirror-a", [a])
        self._register("mirror-b", [b])
        # Written by the interpreter, recorded by no manifest — the blocker.
        self._live_dir("usr/lib/python3.14/site-packages/pkg_a/__pycache__")

        args = argparse.Namespace(
            iso_prep_packages_from=self._list_file("mirror-a", "mirror-b"),
            iso_prep_yes=True, iso_prep_dry_run=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_iso_prep(self.db, args)

        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertFalse(self._exists("usr/lib/python3.14"),
                         "the chain must not survive the prune")
        self.assertIn("emptied director", out)
        self.assertIn("/usr/lib/python3.14/site-packages/pkg_a/__pycache__",
                      out,
                      "every removed path is listed so the shipping-tree "
                      "gate's report stays cross-checkable")

    def test_a_directory_outside_every_removed_path_is_untouched(self):
        """The scope boundary, stated as a test.

        The sweep judges the ancestor closure of what the prune removed and
        the subtrees under it. A directory that is neither — residue from a
        build step somewhere else in the tree — is not this command's to
        remove, and the shipping-tree ownership gate remains the place it
        surfaces.
        """
        a = "usr/lib/python3.14/site-packages/pkg_a/mod.py"
        self._live_file(a)
        self._register("mirror-a", [a])
        self._live_dir("usr/share/unrelated-cache")

        args = argparse.Namespace(
            iso_prep_packages_from=self._list_file("mirror-a"),
            iso_prep_yes=True, iso_prep_dry_run=False)
        with redirect_stdout(io.StringIO()):
            rc = cmd_iso_prep(self.db, args)

        self.assertEqual(rc, 0)
        self.assertTrue(self._exists("usr/share/unrelated-cache"))

    def test_prune_keeps_a_chain_a_shipped_package_still_occupies(self):
        a = "usr/lib/python3.14/site-packages/pkg_a/mod.py"
        kept = "usr/lib/python3.14/site-packages/pkg_kept/mod.py"
        self._live_file(a)
        self._live_file(kept)
        self._register("mirror-a", [a])
        self._register("shipped", [kept])

        args = argparse.Namespace(
            iso_prep_packages_from=self._list_file("mirror-a"),
            iso_prep_yes=True, iso_prep_dry_run=False)
        with redirect_stdout(io.StringIO()):
            rc = cmd_iso_prep(self.db, args)

        self.assertEqual(rc, 0)
        self.assertTrue(self._exists(kept))
        self.assertTrue(self._exists("usr/lib/python3.14/site-packages"))
        self.assertFalse(
            self._exists("usr/lib/python3.14/site-packages/pkg_a"),
            "the pruned package's own emptied directory still goes")

    def test_dry_run_touches_nothing(self):
        a = "usr/lib/python3.14/site-packages/pkg_a/mod.py"
        self._live_file(a)
        self._register("mirror-a", [a], uncompressed_size=1024)
        args = argparse.Namespace(
            iso_prep_packages_from=self._list_file("mirror-a"),
            iso_prep_yes=True, iso_prep_dry_run=True)
        with redirect_stdout(io.StringIO()):
            rc = cmd_iso_prep(self.db, args)
        self.assertEqual(rc, 0)
        self.assertTrue(self._exists(a), "a preview removes nothing")


if __name__ == "__main__":
    unittest.main()

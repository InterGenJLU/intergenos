#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm iso-prep` asserts its own outcome: no pruned payload survives it.

WHAT THIS PINS. The 2026-08-15 from-scratch build reached the shipping-tree
ownership gate with three surviving paths that belonged to packages the prune
had just removed: the /opt/jdk compatibility symlink of a pruned JDK, the
/opt/rocm/llvm compatibility symlink of a pruned compiler package, and the
directory left under them. Both symlinks are DESTDIR payload — their recipes
create them with `ln -s` into DESTDIR — so the prune should have unlinked
them. The cause was determined 2026-08-20 when the assertion reproduced the
pair live: the text manifests record each symlink as a trailing-slash
directory entry (the generator stat-followed the link), the database import
dropped the row entirely, and the database-driven removal therefore never
looked at the path. Removal now consumes the same database∪manifest union
this assertion checks, which heals the class at the removal layer; the
assertion stays, because an outcome check is not made redundant by fixing
one cause of the outcome.

The response to an undetermined cause is not a guess about the cause. It is a
check: the prune states what it did, so the prune proves it. After the
removals, every path the pruned packages were known to own is looked for on
disk. A path that is still there and that no remaining package records is
residue, is named, and fails the prune — at the cheapest discovery point,
attributed to the package that owned it, instead of surfacing later as an
unattributed unowned file at the shipping-tree gate.

WHERE THE KNOWN-OWNED SET COMES FROM, and why it is not just the database.
The prune's own database rows are the obvious source and are not sufficient:
a package whose file rows are missing or incomplete has no rows to check, and
a chroot corpus HAS carried exactly that damage. So the set is the union of
the database rows and the package's on-disk text manifest, read before the
removal takes the manifest away. The two disagree precisely in the case worth
catching.

WHAT MUST NOT TRIP IT. Every path pkm retains ON PURPOSE stays out of the
residue set, or the check would fail correct prunes: paths a remaining package
co-owns, user-modified configuration preserved from deletion, configuration
that could not be read to prove it unmodified, and the top-level FHS skeleton
entries removal refuses on principle. Directories are not this check's
subject either — a surviving directory belongs to the emptied-skeleton sweep,
and a non-empty one legitimately holds another package's payload.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pkm.cli import cmd_iso_prep
from pkm.database import PackageDB

MANIFEST_TEMPLATE = """PACKAGE NAME: {name}-{version}
PACKAGE VERSION: {version}
UNCOMPRESSED SIZE: 1K (1024 bytes)
BUILD DATE: 2026-08-19T00:00:00Z
BUILD SYSTEM: InterGenOS LFS 13.0
DESCRIPTION:
{name}: test fixture

FILE LIST:
{files}
"""


class _Fixture(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        (self.root / "var/lib/igos/packages").mkdir(parents=True)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _live_file(self, rel, content="payload\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def _live_symlink(self, rel, target):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, p)
        return p

    def _register(self, name, paths, version="1.0", manifest_paths=None):
        """Register a package in the database and write its text manifest.

        `manifest_paths` defaults to `paths`; passing a different set is how
        the database-incomplete case is expressed.
        """
        pkg_id = self.db.add_installed(name=name, version=version)
        self.db.add_files(pkg_id, list(paths),
                          hashes={p: "a" * 64 for p in paths})
        entries = list(paths if manifest_paths is None else manifest_paths)
        lines = [e if e.endswith("/") else f"{e} sha256:{'0' * 64}"
                 for e in entries]
        (self.root / "var/lib/igos/packages" / f"{name}-{version}").write_text(
            MANIFEST_TEMPLATE.format(name=name, version=version,
                                     files="\n".join(lines)))
        return pkg_id

    def _list_file(self, *names):
        f = self.tmp / "iso-list.txt"
        f.write_text("\n".join(names) + "\n")
        return str(f)

    def _run(self, *names):
        args = argparse.Namespace(
            iso_prep_packages_from=self._list_file(*names),
            iso_prep_yes=True, iso_prep_dry_run=False)
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = cmd_iso_prep(self.db, args)
        return rc, buf.getvalue() + err.getvalue()

    def _exists(self, rel):
        return os.path.lexists(str(self.root / rel))


class ResidueIsCaught(_Fixture):

    def test_payload_the_database_never_recorded_is_removed_by_the_union(self):
        """The measured shape, healed at the removal layer. The recipe stages
        a compatibility symlink into DESTDIR, the text manifest records it,
        the database rows do not. Removal consumes the union of both records
        — the same union this assertion checks — so the symlink is unlinked
        with the rest of the payload and the outcome assertion has nothing to
        catch. Before the union, removal was database-driven and the symlink
        survived to fail the prune (the 2026-08-20 iso-prep halt)."""
        self._live_file("opt/vendor-jdk/bin/java")
        self._live_symlink("opt/jdk", "vendor-jdk")
        self._register("openjdk", ["opt/vendor-jdk/bin/java"],
                       manifest_paths=["opt/vendor-jdk/bin/java", "opt/jdk"])

        rc, out = self._run("openjdk")

        self.assertEqual(rc, 0,
                         "the union-fed removal should leave the outcome "
                         "assertion nothing to catch:\n" + out)
        self.assertFalse(self._exists("opt/jdk"),
                         "the manifest-recorded symlink is the removal "
                         "path's to unlink, not the assertion's to report")
        self.assertFalse(self._exists("opt/vendor-jdk/bin/java"))

    def test_recorded_file_that_could_not_be_unlinked_fails_the_prune(self):
        """removal already warns about a file it could not unlink, inside a
        long success message, and still returns success. The assertion turns
        that warning into a fact that stops the build."""
        if os.geteuid() == 0:
            self.skipTest("running as root: directory mode does not deny "
                          "the unlink")
        self._live_file("usr/share/locked/payload.dat")
        self._register("mirror-pkg", ["usr/share/locked/payload.dat"])
        os.chmod(self.root / "usr/share/locked", 0o500)
        try:
            rc, out = self._run("mirror-pkg")
        finally:
            os.chmod(self.root / "usr/share/locked", 0o755)

        self.assertNotEqual(rc, 0, out)
        self.assertIn("usr/share/locked/payload.dat", out)

    def test_clean_prune_still_passes_and_says_what_it_verified(self):
        self._live_file("usr/lib/mirror/lib.so")
        self._register("mirror-pkg", ["usr/lib/mirror/lib.so"])

        rc, out = self._run("mirror-pkg")

        self.assertEqual(rc, 0, out)
        self.assertFalse(self._exists("usr/lib/mirror/lib.so"))
        # A pass that does not say how much it checked is indistinguishable
        # from a check that ran over nothing.
        self.assertIn("path(s)", out)


class DeliberateRetentionsDoNotTripIt(_Fixture):

    def test_path_a_remaining_package_co_owns_is_not_residue(self):
        shared = "usr/lib/shared/lib.so"
        self._live_file(shared)
        self._register("mirror-pkg", [shared])
        self._register("shipped-pkg", [shared], version="2.0")

        rc, out = self._run("mirror-pkg")

        self.assertEqual(rc, 0, out)
        self.assertTrue(self._exists(shared),
                        "co-owned payload is retained by design")

    def test_preserved_modified_config_is_not_residue(self):
        cfg = "etc/mirror-pkg/mirror.conf"
        self._live_file(cfg, "original\n")
        pkg_id = self._register("mirror-pkg", [cfg])
        # Registration already records an /etc path as a config file, so this
        # sets the baseline checksum rather than inserting a second row.
        self.db.conn.execute(
            "INSERT OR REPLACE INTO config_files "
            "(package_id, path, original_checksum) VALUES (?, ?, ?)",
            (pkg_id, cfg, "b" * 64))
        self.db.conn.commit()
        # On disk it now differs from the recorded baseline, so removal
        # preserves it rather than destroying a user edit.
        (self.root / cfg).write_text("edited by the user\n")

        rc, out = self._run("mirror-pkg")

        self.assertTrue(self._exists(cfg),
                        "fixture premise: the edited config is preserved")
        self.assertEqual(rc, 0,
                         "a config preserved on purpose is not residue:\n"
                         + out)

    def test_top_level_skeleton_entry_is_not_residue(self):
        """No package legitimately owns a top-level FHS entry, and removal
        refuses to unlink one however the manifest reads. That refusal is
        correct, so it must not read as residue either."""
        self._live_file("usr/lib/mirror/lib.so")
        (self.root / "lib64").mkdir()
        self._register("mirror-pkg", ["usr/lib/mirror/lib.so", "lib64"])

        rc, out = self._run("mirror-pkg")

        self.assertEqual(rc, 0, out)
        self.assertTrue(self._exists("lib64"))

    def test_a_surviving_directory_belongs_to_the_skeleton_sweep(self):
        """A directory is not this check's subject: a non-empty one holds
        somebody's payload, and an empty unowned one is already the
        emptied-skeleton sweep's job."""
        self._live_file("usr/lib/mirror/lib.so")
        self._live_file("usr/lib/mirror/unrecorded-leftover.dat")
        self._register("mirror-pkg",
                       ["usr/lib/mirror/", "usr/lib/mirror/lib.so"])

        rc, out = self._run("mirror-pkg")

        self.assertEqual(rc, 0, out)
        self.assertTrue(self._exists("usr/lib/mirror"),
                        "a directory holding an unrecorded file survives, "
                        "and that is not a residue finding")


if __name__ == "__main__":
    unittest.main()

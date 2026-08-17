#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A18 regression: the iso-prep cross-dep guard reads dep["name"].

cmd_iso_prep aborts when an ISO-shipped (not-in-list) package depends on a
mirror-only (in-list) target — a fail-closed metadata-bug guard. It iterated
`dep["dep_name"]`, but db.get_depends yields {"name","type"} ("dep_name" is the
DB column, not the dict key). So the guard raised KeyError on the first
dependency of any non-target package — crashing the command with an opaque
traceback instead of either passing or aborting with the metadata-bug message.

Fixed: dep["dep_name"] -> dep["name"]. These tests exercise both the cross-dep
ABORT path (must be a clean SystemExit(1), not KeyError) and the common path
(a non-target package with non-target deps must NOT crash the guard).
"""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pkm.cli import cmd_iso_prep
from pkm.database import PackageDB


class IsoPrepCrossDepTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _list_file(self, *names):
        f = self.tmp / "iso-list.txt"
        f.write_text("\n".join(names) + "\n")
        return str(f)

    def test_cross_dep_aborts_cleanly_not_keyerror(self):
        # appbar (NOT in the iso-prep list) depends on libfoo (IN the list).
        # The guard must ABORT with the metadata-bug message — not KeyError.
        self.db.add_installed("libfoo", "1.0", tier="core")
        appbar_id = self.db.add_installed("appbar", "2.0", tier="extra")
        self.db.add_depends(appbar_id, [("libfoo", "runtime")])
        args = argparse.Namespace(
            iso_prep_packages_from=self._list_file("libfoo"))
        buf = io.StringIO()
        with redirect_stderr(buf), self.assertRaises(SystemExit) as ctx:
            cmd_iso_prep(self.db, args)
        self.assertEqual(ctx.exception.code, 1)
        err = buf.getvalue()
        self.assertIn("depend on MIRROR-only", err)
        self.assertIn("appbar", err)
        self.assertIn("libfoo", err)

    def test_non_target_deps_do_not_crash_guard(self):
        # appbar (non-target) depends on libexternal (non-target). The guard
        # must iterate appbar's deps WITHOUT KeyError, find no cross-dep, and
        # the command proceeds to the dry-run plan for the real target.
        self.db.add_installed("libfoo", "1.0", tier="core",
                              uncompressed_size=1024)
        appbar_id = self.db.add_installed("appbar", "2.0", tier="extra")
        self.db.add_depends(appbar_id, [("libexternal", "runtime")])
        args = argparse.Namespace(
            iso_prep_packages_from=self._list_file("libfoo"),
            iso_prep_dry_run=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_iso_prep(self.db, args)
        self.assertEqual(rc, 0)
        self.assertIn("removal plan", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

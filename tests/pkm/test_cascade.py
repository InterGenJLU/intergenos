#!/usr/bin/env python3
"""Tests for pkm.cascade reverse-dependency upgrade warning (O-033 Phase 1)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.cascade import format_reverse_dep_warning, MAX_LISTED_REVERSE_DEPS


class TestFormatReverseDepWarning(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-cascade-")
        self.db = PackageDB(Path(self.tmp) / "test.db", root=self.tmp)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _install_with_dep(self, name, dep_name):
        pkg_id = self.db.add_installed(name, "1.0", "archive", "/dev/null", commit=True)
        self.db.add_depends(pkg_id, [(dep_name, "runtime")], commit=True)

    def test_no_rdeps_returns_empty(self):
        # glibc with zero reverse-dependents.
        self.assertEqual(format_reverse_dep_warning(self.db, "glibc"), "")

    def test_few_rdeps_lists_names(self):
        self._install_with_dep("vim", "glibc")
        self._install_with_dep("nano", "glibc")
        self._install_with_dep("bash", "glibc")
        warning = format_reverse_dep_warning(self.db, "glibc")
        self.assertIn("3 reverse-dependent", warning)
        self.assertIn("bash", warning)
        self.assertIn("nano", warning)
        self.assertIn("vim", warning)
        # Sorted alphabetically
        i_bash = warning.index("bash")
        i_nano = warning.index("nano")
        i_vim = warning.index("vim")
        self.assertLess(i_bash, i_nano)
        self.assertLess(i_nano, i_vim)

    def test_many_rdeps_collapses_to_count_summary(self):
        # Install MAX_LISTED_REVERSE_DEPS + 1 reverse-dependents to trigger
        # the count-summary collapse path.
        n = MAX_LISTED_REVERSE_DEPS + 5
        for i in range(n):
            self._install_with_dep(f"pkg{i:03d}", "glibc")
        warning = format_reverse_dep_warning(self.db, "glibc")
        self.assertIn(f"{n} reverse-dependent", warning)
        # Should NOT contain the full listing
        self.assertNotIn("pkg000, pkg001", warning)
        # Should hint at pkm depends --reverse for the full list
        self.assertIn("pkm depends --reverse glibc", warning)
        # Should include dep-type counter
        self.assertIn("runtime", warning)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for B4 staging-path validation in PackageTracker._validate_staging_paths.

Covers the post-2026-05-02 expansion of the check that distinguishes
intra-package absolute symlinks (legitimate, e.g., xkeyboard-config's
/usr/share/X11/xkb -> /usr/share/xkeyboard-config-2 compat symlink) from
real staging-escape attempts.
"""

import importlib
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

# igos-build has a hyphen in its directory name, so it can't be imported via
# `from igos-build.tracker import X` syntax. Use importlib to load it
# (same pattern as scripts/apply-dep-audit.py).
sys.path.insert(0, "/mnt/intergenos")
_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker


def _make_tracker():
    """Construct a minimal PackageTracker stub for isolated testing."""
    t = PackageTracker()
    t.logger = logging.getLogger("test_tracker_b4")
    return t


def _make_pkg(name="testpkg", version="1.0"):
    return SimpleNamespace(name=name, version=version)


class TestB4StagingValidation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.staging = Path(self.tmp) / "stage"
        self.staging.mkdir()
        self.tracker = _make_tracker()
        self.pkg = _make_pkg()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- positive cases (should pass) ---

    def test_empty_staging_passes(self):
        self.assertTrue(
            self.tracker._validate_staging_paths(self.pkg, self.staging)
        )

    def test_regular_files_only_passes(self):
        (self.staging / "usr").mkdir()
        (self.staging / "usr/bin").mkdir()
        (self.staging / "usr/bin/foo").write_text("binary content")
        (self.staging / "usr/share").mkdir()
        (self.staging / "usr/share/foo.txt").write_text("data")
        self.assertTrue(
            self.tracker._validate_staging_paths(self.pkg, self.staging)
        )

    def test_intra_package_absolute_symlink_passes(self):
        """The xkeyboard-config case: absolute symlink to a path also installed
        by this package. Should pass."""
        (self.staging / "usr/share/xkeyboard-config-2").mkdir(parents=True)
        (self.staging / "usr/share/xkeyboard-config-2/data").write_text("d")
        (self.staging / "usr/share/X11").mkdir()
        os.symlink(
            "/usr/share/xkeyboard-config-2",
            self.staging / "usr/share/X11/xkb"
        )
        self.assertTrue(
            self.tracker._validate_staging_paths(self.pkg, self.staging)
        )

    def test_relative_symlink_within_staging_passes(self):
        (self.staging / "usr/lib").mkdir(parents=True)
        (self.staging / "usr/lib/libfoo.so.1").write_text("lib")
        os.symlink("libfoo.so.1", self.staging / "usr/lib/libfoo.so")
        self.assertTrue(
            self.tracker._validate_staging_paths(self.pkg, self.staging)
        )

    def test_intra_package_relative_complex_symlink_passes(self):
        """Relative symlink whose resolution post-deploy lands in this
        package's manifest. Pre-deploy it appears to escape staging via ..
        but the deployed semantic is intra-package. Should pass."""
        (self.staging / "usr/share/intergen").mkdir(parents=True)
        (self.staging / "usr/share/intergen/data").write_text("d")
        (self.staging / "usr/lib/intergen").mkdir(parents=True)
        # Symlink at /usr/lib/intergen/data -> ../../share/intergen/data.
        # Pre-deploy this ../../share resolves to staging_root/share which
        # IS inside staging — but if not, the post-deploy lookup would catch.
        os.symlink(
            "../../share/intergen/data",
            self.staging / "usr/lib/intergen/data"
        )
        self.assertTrue(
            self.tracker._validate_staging_paths(self.pkg, self.staging)
        )

    def test_cross_package_absolute_symlink_warns_but_passes(self):
        """Symlink to absolute path NOT in this package's manifest gets a
        warning but allows under current cross-package policy."""
        (self.staging / "usr/lib").mkdir(parents=True)
        os.symlink("/etc/some-other-package-config",
                   self.staging / "usr/lib/somelink")
        # Should pass with a warning logged
        with self.assertLogs("test_tracker_b4", level="WARNING") as cm:
            result = self.tracker._validate_staging_paths(self.pkg, self.staging)
        self.assertTrue(result)
        self.assertTrue(any("not in this package's manifest" in m
                            for m in cm.output))

    # --- negative cases (should reject) ---

    def test_relative_symlink_escaping_to_unowned_path_rejected(self):
        """Relative symlink that escapes staging AND the post-deploy target
        is not in this package's manifest. Real escape attempt — REJECT."""
        (self.staging / "usr/lib").mkdir(parents=True)
        # Symlink at /usr/lib/bad -> ../../../../etc/passwd
        # Post-deploy: /usr/lib/bad -> /etc/passwd (not owned by this package).
        os.symlink("../../../etc/passwd", self.staging / "usr/lib/bad")
        with self.assertLogs("test_tracker_b4", level="ERROR") as cm:
            result = self.tracker._validate_staging_paths(self.pkg, self.staging)
        self.assertFalse(result)
        self.assertTrue(any("escapes staging" in m for m in cm.output))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A verification that cannot run must say so, never report the file absent.

verify_package answered every path with os.path.lexists(), which returns
False both when a path is not there and when the caller is not permitted to
look. Any user who is not root verifying a package whose files sit under a
directory they may not search was therefore told those files were MISSING —
a fright about a healthy system, produced by the tool that is supposed to
establish the system is healthy. A second copy of the same fault sat in the
content check: when the hash could not be computed the file was skipped
silently, which counted an unperformed check as a passed one.

Both now report `undeterminable`, and `undeterminable` is neither "verified"
nor "failed" in the exit status.

Scope pins:
  - a path under an unsearchable parent -> undeterminable, NOT missing
  - a present file whose bytes cannot be read -> undeterminable, NOT verified
  - a genuinely absent file -> still missing (the fix must not mask faults)
  - a real fault alongside an unreadable file -> EXIT_MODIFIED, because a
    failure outranks an unknown
  - clean package -> EXIT_OK; only-unknowns -> EXIT_UNDETERMINED
  - the CLI turns those three into exit 0 / 1 / 3

The unreadable cases are produced with permission bits and are meaningless
as root, which bypasses them; those tests skip when the suite runs as root
and say why rather than passing vacuously.
"""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from pkm.database import (
    PATH_ABSENT,
    PATH_PRESENT,
    PATH_UNDETERMINABLE,
    PackageDB,
    _probe_path,
)
from pkm.verifier import (
    EXIT_MODIFIED,
    EXIT_OK,
    EXIT_UNDETERMINED,
    PackageVerifier,
)

RUNNING_AS_ROOT = (os.geteuid() == 0)
ROOT_SKIP = ("root bypasses the permission bits this test relies on; the "
             "condition under test is a NON-root read of a file it may not "
             "open")


class ProbePathTests(unittest.TestCase):
    """The seam itself: three answers, and refusal never reads as absence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        # Restore any mode we cleared, or the temp dir cannot be removed.
        for p in self.root.rglob("*"):
            try:
                p.chmod(0o755)
            except OSError:
                pass
        self.tmp.cleanup()

    def test_present_file(self):
        f = self.root / "present"
        f.write_bytes(b"x")
        self.assertEqual(_probe_path(str(f)), PATH_PRESENT)

    def test_absent_file(self):
        self.assertEqual(_probe_path(str(self.root / "nope")), PATH_ABSENT)

    def test_dangling_symlink_is_present_not_absent(self):
        # lstat sees the link itself. The link exists; its target does not.
        link = self.root / "dangling"
        link.symlink_to(self.root / "nowhere")
        self.assertEqual(_probe_path(str(link)), PATH_PRESENT)

    def test_path_under_a_non_directory_is_absent(self):
        # ENOTDIR is a real answer: nothing can exist below a regular file.
        f = self.root / "regular"
        f.write_bytes(b"x")
        self.assertEqual(_probe_path(str(f / "child")), PATH_ABSENT)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_unsearchable_parent_is_undeterminable_not_absent(self):
        locked = self.root / "locked"
        locked.mkdir()
        (locked / "hidden").write_bytes(b"payload")
        locked.chmod(0o000)
        self.assertEqual(_probe_path(str(locked / "hidden")),
                         PATH_UNDETERMINABLE)


class VerifyUndeterminableTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        (self.root / "usr" / "bin").mkdir(parents=True)
        self.db = PackageDB(Path(self.tmp.name) / "t.db", root=str(self.root))
        self.verifier = PackageVerifier(self.db)

    def tearDown(self):
        self.db.close()
        for p in sorted(self.root.rglob("*"), reverse=True):
            try:
                p.chmod(0o755)
            except OSError:
                pass
        self.tmp.cleanup()

    def _install(self, name, relpaths):
        """Register a package whose files exist, so hashes get recorded."""
        for rel in relpaths:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"payload-" + rel.encode())
        pkg_id = self.db.add_installed(name, "1.0", release=1, tier="core")
        self.db.add_files(pkg_id, list(relpaths))
        return pkg_id

    def test_clean_package_is_ok(self):
        self._install("alpha", ["usr/bin/alpha"])
        result = self.verifier.verify("alpha", mode="strict")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["undeterminable"], [])
        self.assertEqual(result["exit_code"], EXIT_OK)

    def test_genuinely_absent_file_is_still_missing(self):
        # The fix must not launder real faults into "could not determine".
        self._install("beta", ["usr/bin/beta"])
        (self.root / "usr" / "bin" / "beta").unlink()
        result = self.verifier.verify("beta", mode="strict")
        self.assertEqual(result["missing"], ["usr/bin/beta"])
        self.assertEqual(result["undeterminable"], [])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_file_under_unsearchable_dir_is_undeterminable_not_missing(self):
        self._install("gamma", ["usr/lib/private/gamma.so"])
        (self.root / "usr" / "lib" / "private").chmod(0o000)
        result = self.verifier.verify("gamma", mode="strict")
        self.assertEqual(result["missing"], [],
                         "an unreadable file must never be called missing")
        self.assertEqual(result["undeterminable"], ["usr/lib/private/gamma.so"])
        self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_unreadable_file_content_is_undeterminable_not_verified(self):
        # Present and stat-able, but its bytes cannot be read: the content
        # check did not happen, and must not be counted as though it passed.
        self._install("delta", ["usr/bin/delta"])
        (self.root / "usr" / "bin" / "delta").chmod(0o000)
        result = self.verifier.verify("delta", mode="strict")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["undeterminable"], ["usr/bin/delta"])
        self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_real_failure_outranks_an_unknown(self):
        self._install("epsilon", ["usr/bin/eps-gone", "usr/bin/eps-locked"])
        (self.root / "usr" / "bin" / "eps-gone").unlink()
        (self.root / "usr" / "bin" / "eps-locked").chmod(0o000)
        result = self.verifier.verify("epsilon", mode="strict")
        self.assertEqual(result["missing"], ["usr/bin/eps-gone"])
        self.assertEqual(result["undeterminable"], ["usr/bin/eps-locked"])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_fast_mode_still_reads_existence_honestly(self):
        # fast mode skips content, so an unreadable-content file is fine,
        # but an unsearchable parent still leaves existence unknown.
        self._install("zeta", ["usr/lib/private/zeta.so"])
        (self.root / "usr" / "lib" / "private").chmod(0o000)
        result = self.verifier.verify("zeta", mode="fast")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["undeterminable"], ["usr/lib/private/zeta.so"])
        self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_root_only_file_read_as_non_root(self):
        """The dispatch's named case: a non-root run over a root-only file.

        A real root-owned 0600 file cannot be created without root, so this
        reproduces the identical kernel condition — the current user is not
        permitted to open the file — with permission bits it does own. The
        code path exercised is the same EACCES from open().
        """
        self._install("eta", ["usr/bin/eta"])
        target = self.root / "usr" / "bin" / "eta"
        target.chmod(0o000)
        self.assertFalse(os.access(str(target), os.R_OK),
                         "precondition: this user must not be able to read it")
        self.assertTrue(stat.S_ISREG(os.lstat(str(target)).st_mode),
                        "precondition: the file is present and regular")
        result = self.verifier.verify("eta", mode="strict")
        self.assertEqual(result["undeterminable"], ["usr/bin/eta"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)


class VerifyCliExitCodeTests(unittest.TestCase):
    """The three outcomes must be three different process exit codes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        (self.root / "usr" / "bin").mkdir(parents=True)
        self.db = PackageDB(Path(self.tmp.name) / "t.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        for p in sorted(self.root.rglob("*"), reverse=True):
            try:
                p.chmod(0o755)
            except OSError:
                pass
        self.tmp.cleanup()

    def _install(self, name, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"payload")
        pkg_id = self.db.add_installed(name, "1.0", release=1, tier="core")
        self.db.add_files(pkg_id, [rel])
        return pkg_id

    def _run(self, package):
        from pkm import cli

        class _Args:
            verify_all = False
            verify_mode = "strict"
            verify_detail = False

        args = _Args()
        args.package = package
        try:
            cli.cmd_verify(self.db, args)
        except SystemExit as exc:
            return exc.code
        return 0

    def test_clean_exits_zero(self):
        self._install("alpha", "usr/bin/alpha")
        self.assertEqual(self._run("alpha"), 0)

    def test_missing_exits_one(self):
        self._install("beta", "usr/bin/beta")
        (self.root / "usr" / "bin" / "beta").unlink()
        self.assertEqual(self._run("beta"), 1)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_undeterminable_exits_three_not_one(self):
        self._install("gamma", "usr/bin/gamma")
        (self.root / "usr" / "bin" / "gamma").chmod(0o000)
        self.assertEqual(
            self._run("gamma"), 3,
            "a check that could not run must not share an exit code with a "
            "check that failed")


if __name__ == "__main__":
    unittest.main()

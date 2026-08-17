# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""GBC003 G3-8B: pkm extraction must not let a unit-enable symlink clobber the
mode of the real unit file it points at.

CPython's tarfile.extractall() applies os.chmod() to symlink members, and on
Linux os.chmod() has no lchmod equivalent so it FOLLOWS the link. A relative
symlink like `sysinit.target.wants/cryptsetup.target -> ../cryptsetup.target`
therefore chmods the REAL cryptsetup.target to the symlink's data_filter-clamped
mode (0o777 & ~0o022 = 0o755). On the GBC002.6 install this turned exactly the 3
.target units carrying a sysinit.target.wants symlink (cryptsetup, imports,
integritysetup) into 0755, which systemd warns about as "marked executable".

Whether tarfile performs that follow-chmod is micro-version dependent (observed
on the build VM's 3.12.3 and the live ISO's 3.14, NOT on every 3.12 build), so
these tests exercise the restore logic _restore_symlink_target_modes()
DIRECTLY against a simulated post-clobber filesystem — a deterministic guard on
any host — plus a best-effort end-to-end extraction smoke test.
"""

import io
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path

from pkm.installer import _restore_symlink_target_modes, _safe_extract_tar


def _file_member(name, mode):
    ti = tarfile.TarInfo(name)
    ti.type = tarfile.REGTYPE
    ti.mode = mode
    ti.size = 0
    return ti


def _sym_member(name, target):
    ti = tarfile.TarInfo(name)
    ti.type = tarfile.SYMTYPE
    ti.mode = 0o777
    ti.linkname = target
    return ti


class TestRestoreSymlinkTargetModes(unittest.TestCase):
    """Deterministic unit test of the restore pass — host-tarfile-independent."""

    def setUp(self):
        self.dest = Path(tempfile.mkdtemp())
        self.sysd = self.dest / "usr/lib/systemd/system"
        (self.sysd / "sysinit.target.wants").mkdir(parents=True)
        # Simulate the POST-CLOBBER state: the real unit was made 0755 by the
        # symlink-follow chmod; a peer unit with no enable-symlink is 0644.
        self.unit = self.sysd / "cryptsetup.target"
        self.unit.write_text("# unit\n")
        os.chmod(self.unit, 0o755)
        self.peer = self.sysd / "cryptsetup-pre.target"
        self.peer.write_text("# unit\n")
        os.chmod(self.peer, 0o644)
        # The enable symlink that caused the clobber.
        os.symlink("../cryptsetup.target",
                   self.sysd / "sysinit.target.wants/cryptsetup.target")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dest)

    def _mode(self, p):
        return stat.S_IMODE(os.lstat(p).st_mode)

    def test_clobbered_unit_restored_to_archive_mode(self):
        members = [
            _file_member("usr/lib/systemd/system/cryptsetup.target", 0o644),
            _file_member("usr/lib/systemd/system/cryptsetup-pre.target", 0o644),
            _sym_member(
                "usr/lib/systemd/system/sysinit.target.wants/cryptsetup.target",
                "../cryptsetup.target"),
        ]
        _restore_symlink_target_modes(members, self.dest)
        self.assertEqual(self._mode(self.unit), 0o644,
                         "symlink-target unit was not restored to its archive mode")

    def test_non_targeted_file_untouched(self):
        # A file that is NOT a symlink target must be left exactly as-is, even
        # if it happens to be executable — the pass is surgical.
        os.chmod(self.peer, 0o755)
        members = [
            _file_member("usr/lib/systemd/system/cryptsetup.target", 0o644),
            _file_member("usr/lib/systemd/system/cryptsetup-pre.target", 0o755),
            _sym_member(
                "usr/lib/systemd/system/sysinit.target.wants/cryptsetup.target",
                "../cryptsetup.target"),
        ]
        _restore_symlink_target_modes(members, self.dest)
        self.assertEqual(self._mode(self.peer), 0o755)
        self.assertEqual(self._mode(self.unit), 0o644)

    def test_the_symlink_itself_is_not_followed_for_mode(self):
        # The restore must chmod the real file, never the symlink path (which
        # would re-trigger the follow-chmod). Symlink stays a symlink.
        members = [
            _file_member("usr/lib/systemd/system/cryptsetup.target", 0o644),
            _sym_member(
                "usr/lib/systemd/system/sysinit.target.wants/cryptsetup.target",
                "../cryptsetup.target"),
        ]
        _restore_symlink_target_modes(members, self.dest)
        link = self.sysd / "sysinit.target.wants/cryptsetup.target"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "../cryptsetup.target")

    def test_no_symlinks_is_a_noop(self):
        os.chmod(self.unit, 0o755)
        members = [_file_member("usr/lib/systemd/system/cryptsetup.target", 0o644)]
        _restore_symlink_target_modes(members, self.dest)
        # No symlink targets cryptsetup.target -> left as found (0755).
        self.assertEqual(self._mode(self.unit), 0o755)


class TestEndToEndExtraction(unittest.TestCase):
    """Smoke test: a full _safe_extract_tar of an archive with the clobber
    structure ends with the unit at 0644. On hosts whose tarfile does not
    follow-chmod symlinks this passes trivially (the fix is a no-op); on hosts
    that do, it proves the wiring."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "dest"
        (self.dest / "usr/lib/systemd/system").mkdir(parents=True)
        for ln, t in (("lib", "usr/lib"), ("bin", "usr/bin"),
                      ("sbin", "usr/sbin"), ("lib64", "usr/lib64")):
            os.symlink(t, self.dest / ln)
        self.archive = self.tmp / "pkg.tar.gz"
        data = b"# unit\n"
        with tarfile.open(self.archive, "w:gz") as tf:
            for nm in ("cryptsetup.target", "cryptsetup-pre.target"):
                ti = _file_member(f"usr/lib/systemd/system/{nm}", 0o644)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            tf.addfile(_sym_member(
                "usr/lib/systemd/system/sysinit.target.wants/cryptsetup.target",
                "../cryptsetup.target"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_unit_not_executable_after_extract(self):
        ok, err = _safe_extract_tar(self.archive, self.dest)
        self.assertTrue(ok, err)
        unit = self.dest / "usr/lib/systemd/system/cryptsetup.target"
        self.assertEqual(stat.S_IMODE(os.lstat(unit).st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()

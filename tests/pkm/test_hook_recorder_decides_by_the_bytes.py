# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The hook-output recorder decides a rewrite by the bytes (D-9b).

A lifecycle hook step that changes only a file's metadata (its mode, its owner,
its times, an extended attribute such as a file capability, its link count, or
the inode the same bytes live in) still moves the file's ctime, and ctime is
what the recorder's whole-tree diff reads as "something happened here". That
alone used to reclassify the package's own file as hook-managed content, which
is existence-checked only: after a `chmod 0700` in a post_install the file's
bytes were never compared again, and `pkm verify` said ok over whatever the
file held from then on.

The recorder now records what each of the package's own payload paths IS at
the snapshot before the hook (a regular file's sha256, a symbolic link's
target) and compares it after the hook. Only a path whose bytes changed (a
link whose target changed, a path that became another kind of entry) becomes
hook-managed, as before. A metadata-only change keeps the file content-checked
with the recorded hash the deploy's, and the transcript says it in one line
naming the path and what changed. Every case reads `pkm verify` afterwards.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pkm.database import PackageDB
from pkm.installer import PackageInstaller

PAYLOAD = "usr/bin/modetest"
PAYLOAD_BYTES = b"#!/bin/sh\necho mode test\n"
TAMPERED_BYTES = b"#!/bin/sh\necho not what the archive shipped\n"
LINK = "usr/bin/modelink"
ALIAS = "usr/bin/modetest-alias"
TARGET = '"$PKM_PACKAGE_ROOT/usr/bin/modetest"'
LINK_TARGET = '"$PKM_PACKAGE_ROOT/usr/bin/modelink"'
SETCAP = "/usr/sbin/setcap"
UNSHARE = "/usr/bin/unshare"


def _setcap_command():
    """The command that sets a file capability from a hook on this host.

    A process with CAP_SETFCAP runs setcap directly. An unprivileged test
    process can set one from a user namespace it owns (the kernel records a
    namespaced capability on a file the namespace's root owns). Returns None
    when neither is possible, measured on a probe file, so the case skips with
    the reason rather than passing without the step it names.
    """
    if not os.access(SETCAP, os.X_OK):
        return None, f"{SETCAP} is not on this host"
    candidates = [[SETCAP]]
    if os.access(UNSHARE, os.X_OK):
        candidates.append([UNSHARE, "-r", SETCAP])
    probe_dir = tempfile.mkdtemp()
    try:
        probe = os.path.join(probe_dir, "probe")
        reasons = []
        for prefix in candidates:
            with open(probe, "wb") as f:
                f.write(b"probe\n")
            done = subprocess.run(prefix + ["cap_net_raw+ep", probe],
                                  capture_output=True, text=True)
            if done.returncode == 0 and "security.capability" in os.listxattr(probe):
                return " ".join(prefix), ""
            reasons.append(f"{' '.join(prefix)}: rc {done.returncode} "
                           f"{(done.stdout + done.stderr).strip()}")
            os.unlink(probe)
        return None, "this host lets the test process set no file capability (" + "; ".join(reasons) + ")"
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def _another_group():
    """A group this process may give its own file, other than its own group."""
    own = os.getegid()
    for gid in sorted(os.getgroups()):
        if gid != own:
            return gid
    return None


def _user_xattrs_work(directory):
    probe = os.path.join(directory, "xattr-probe")
    try:
        with open(probe, "wb") as f:
            f.write(b"probe\n")
        os.setxattr(probe, "user.probe", b"1")
        return True
    except OSError:
        return False
    finally:
        if os.path.exists(probe):
            os.unlink(probe)


class _Harness(unittest.TestCase):
    """A real PackageInstaller over a real archive with a hook, on a scratch root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "t.db", root=str(self.root))
        self.addCleanup(self.db.close)
        self.installer = PackageInstaller(self.db, root=str(self.root))

    def _install(self, hook_body, link=False):
        stg = self.tmp / "stg"
        payload = stg / PAYLOAD
        payload.parent.mkdir(parents=True)
        payload.write_bytes(PAYLOAD_BYTES)
        payload.chmod(0o755)
        if link:
            os.symlink("modetest", stg / LINK)
        (stg / ".PKGINFO").write_text("pkgname = modetest\npkgver = 1.0\n")
        scripts = stg / ".scripts"
        scripts.mkdir()
        hook = scripts / "post_install.sh"
        hook.write_text("#!/bin/bash\nset -e\n" + hook_body)
        hook.chmod(0o755)
        arc = self.tmp / "modetest-1.0.igos.tar.gz"
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(stg, arcname=".")
        ok, msg = self.installer.install("modetest", archive_path=str(arc))
        self.assertTrue(ok, msg)
        return msg

    def _row(self, path):
        return self.db.conn.execute(
            "SELECT is_generated, checksum FROM files WHERE path = ?",
            (path,)).fetchone()

    def _record_lines(self, msg, path):
        return [line for line in msg.splitlines()
                if "hook[record]" in line and f" {path}" in line]

    def assert_kept_content_checked(self, msg, path, change, kept="the bytes"):
        row = self._row(path)
        self.assertIsNotNone(row, f"{path} has no row:\n{msg}")
        self.assertEqual(
            row[0], 0,
            f"a hook step that changed no byte of {path} turned its content "
            f"check off (hook-managed, existence-checked):\n{msg}")
        lines = self._record_lines(msg, path)
        self.assertEqual(len(lines), 1,
                         f"one line naming {path} and what changed:\n{msg}")
        self.assertIn(change, lines[0])
        self.assertIn(f"not {kept}", lines[0])
        self.assertNotIn("hook-managed", lines[0])

    def assert_payload_bytes_still_verified(self, msg):
        self.assertEqual(
            self._row(PAYLOAD)[1], hashlib.sha256(PAYLOAD_BYTES).hexdigest(),
            "the recorded hash is no longer the deploy's")
        clean = self.db.verify_package("modetest")
        self.assertEqual(clean["modified"], [], msg)
        self.assertNotIn(PAYLOAD, clean["generated"], msg)
        (self.root / PAYLOAD).write_bytes(TAMPERED_BYTES)
        after = self.db.verify_package("modetest")
        self.assertEqual(
            after["modified"], [PAYLOAD],
            "the file's bytes changed after the hook and verify did not see it")


class AMetadataOnlyStepKeepsTheContentCheck(_Harness):

    def test_a_chmod_only_step_keeps_the_file_content_checked(self):
        msg = self._install(f"chmod 0700 {TARGET}\n")
        self.assertEqual(os.stat(self.root / PAYLOAD).st_mode & 0o7777, 0o700)
        self.assert_kept_content_checked(msg, PAYLOAD, "the mode (0755 -> 0700)")
        self.assert_payload_bytes_still_verified(msg)

    def test_a_touch_only_step_keeps_the_file_content_checked(self):
        msg = self._install(f'touch -d "2026-01-01 00:00:00" {TARGET}\n')
        self.assert_kept_content_checked(msg, PAYLOAD, "the modification time")
        self.assert_payload_bytes_still_verified(msg)

    def test_a_setcap_only_step_keeps_the_file_content_checked(self):
        command, reason = _setcap_command()
        if command is None:
            self.skipTest(reason)
        msg = self._install(f"{command} cap_net_raw+ep {TARGET}\n")
        self.assertIn("security.capability",
                      os.listxattr(self.root / PAYLOAD))
        self.assert_kept_content_checked(
            msg, PAYLOAD, "the extended attributes (security.capability)")
        self.assert_payload_bytes_still_verified(msg)

    def test_a_chgrp_only_step_keeps_the_file_content_checked(self):
        gid = _another_group()
        if gid is None:
            self.skipTest("this process belongs to no second group to give a file")
        msg = self._install(f"chgrp {gid} {TARGET}\n")
        self.assertEqual(os.stat(self.root / PAYLOAD).st_gid, gid)
        self.assert_kept_content_checked(
            msg, PAYLOAD, f"-> {os.geteuid()}:{gid})")
        self.assertIn("the owner (", self._record_lines(msg, PAYLOAD)[0])
        self.assert_payload_bytes_still_verified(msg)

    def test_an_extended_attribute_only_step_keeps_the_file_content_checked(self):
        if not _user_xattrs_work(str(self.tmp)):
            self.skipTest("this filesystem keeps no user extended attribute")
        setter = (f'{sys.executable} -I -c "import os, sys; '
                  f"os.setxattr(sys.argv[1], 'user.demo', b'1')\" {TARGET}\n")
        msg = self._install(setter)
        self.assert_kept_content_checked(
            msg, PAYLOAD, "the extended attributes (user.demo)")
        self.assert_payload_bytes_still_verified(msg)

    def test_a_new_hard_link_to_the_file_keeps_it_content_checked(self):
        msg = self._install(
            f'ln {TARGET} "$PKM_PACKAGE_ROOT/{ALIAS}"\n')
        self.assert_kept_content_checked(msg, PAYLOAD, "the link count (1 -> 2)")
        self.assertEqual(self._row(ALIAS)[0], 1,
                         "the name the hook created is its output, as before")
        self.assert_payload_bytes_still_verified(msg)

    def test_the_same_bytes_in_a_new_file_keep_the_content_check(self):
        msg = self._install(
            f'cp -p {TARGET} "$PKM_PACKAGE_ROOT/{PAYLOAD}.new"\n'
            f'mv -f "$PKM_PACKAGE_ROOT/{PAYLOAD}.new" {TARGET}\n')
        self.assert_kept_content_checked(
            msg, PAYLOAD, "the inode (the same bytes in a new file)")
        self.assert_payload_bytes_still_verified(msg)

    def test_a_symlink_whose_times_alone_changed_keeps_its_class(self):
        msg = self._install(
            f'touch -h -d "2026-01-01 00:00:00" {LINK_TARGET}\n', link=True)
        self.assert_kept_content_checked(
            msg, LINK, "the modification time", kept="the target")
        self.assertNotIn(LINK, self.db.verify_package("modetest")["generated"])


class ARewriteIsStillHookManaged(_Harness):
    """The positive controls: what changed the bytes is recorded as before."""

    def assert_hook_managed(self, msg, path):
        row = self._row(path)
        self.assertIsNotNone(row, msg)
        self.assertEqual(row[0], 1, f"a rewrite of {path} lost its class:\n{msg}")
        lines = self._record_lines(msg, path)
        self.assertEqual(len(lines), 1, msg)
        self.assertIn("rewritten by its hook", lines[0])
        self.assertIn(path, self.db.verify_package("modetest")["generated"])

    def test_a_hook_that_rewrites_the_bytes_is_still_hook_managed(self):
        msg = self._install(f'printf "%s\\n" "a hook wrote this" > {TARGET}\n')
        self.assert_hook_managed(msg, PAYLOAD)
        self.assertEqual(self.db.verify_package("modetest")["modified"], [])

    def test_a_rewrite_that_also_changes_the_mode_is_hook_managed(self):
        msg = self._install(
            f'printf "%s\\n" "a hook wrote this" > {TARGET}\nchmod 0700 {TARGET}\n')
        self.assert_hook_managed(msg, PAYLOAD)

    def test_a_symlink_the_hook_points_elsewhere_is_hook_managed(self):
        # A new target text that still resolves, so verify reaches the
        # class rather than reporting the link dangling.
        msg = self._install(f"ln -sfn ./modetest {LINK_TARGET}\n", link=True)
        self.assertEqual(os.readlink(self.root / LINK), "./modetest")
        self.assert_hook_managed(msg, LINK)


if __name__ == "__main__":
    unittest.main()

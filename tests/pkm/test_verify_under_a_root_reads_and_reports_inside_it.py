# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm --root R verify` reads and reports inside the root it verifies.

Each shape here is built in a scratch root and checked through the real
command, the way a person runs it against an offline root.

THE WHOLE-MACHINE FORM NAMES WHAT IT COULD NOT FOLLOW. `pkm verify --all`
printed a count per package ("1 could not be checked") with no path and no
reason, --detail included, and closed with "Re-run as root", which cannot make
an offline root's /run appear. Each link that could not be followed is now
named with its text and its reason, --detail lists the path behind every count,
and the closing line offers root only when a file this user cannot read is
among the unknowns.

STRICT MODE READS A LINK'S BYTES INSIDE THE ROOT. The content check opened
<root>/<path>, and the kernel of the machine running the check followed an
absolute link text to its own file: an intact root reported "modified", and a
changed one passed when the host's file matched the recorded hash. The bytes
are now read where the walk inside the root leads.

A CONTENT READ NEVER WAITS. A FIFO where a hash is recorded held strict verify
open forever. The read now opens without blocking, and anything that is not a
regular file where a hash is recorded is reported modified: it is not the file
that was installed.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pkm import rootpaths
from pkm.database import PackageDB

REPO = Path(__file__).resolve().parents[2]
RUNNING_AS_ROOT = (os.geteuid() == 0)
ROOT_SKIP = ("root reads a mode-000 file; the condition under test is a "
             "non-root user who may not read one")
# A strict verify that waits on a FIFO never returns; the limit turns that
# into a failure instead of a hung suite.
TIMEOUT = 15


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class _Root(unittest.TestCase):
    """A scratch root with its own package database, verified through the
    real command."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.top = Path(self.tmp.name)
        self.root = self.top / "root"
        self.root.mkdir()
        self.db = None
        self._locked = []

    def tearDown(self):
        if self.db is not None:
            self.db.close()
        for p in self._locked:
            p.chmod(0o644)
        self.tmp.cleanup()

    def _file(self, rel, data=b"payload\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def _link(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(text, p)
        return p

    def _own(self, name, relpaths, hashes=None):
        if self.db is None:
            self.db = PackageDB(str(rootpaths.db_path(self.root)),
                                root=str(self.root))
        pkg_id = self.db.add_installed(name, "1.0", release=1, tier="core")
        self.db.add_files(pkg_id, list(relpaths), hashes=hashes)

    def _pkm(self, *args):
        # The command opens the database itself; a connection this test still
        # holds would be a second process on it.
        if self.db is not None:
            self.db.close()
            self.db = None
        env = {k: v for k, v in os.environ.items()
               if k not in ("IGOS_TRACE_RUNID", "IGOS_TRACE_ROOT")}
        env.update(PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1",
                   COLUMNS="100")
        try:
            res = subprocess.run(
                [sys.executable, "-P", "-m", "pkm", "--root", str(self.root),
                 *args],
                capture_output=True, text=True, env=env, timeout=TIMEOUT,
                stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            self.fail(f"`pkm {' '.join(args)}` did not return within "
                      f"{TIMEOUT} s: the content check waited on what it read")
        return res.returncode, res.stdout + res.stderr


class TheWholeMachineFormNamesWhatItCouldNotFollow(_Root):
    """W18: an offline root whose run/ is a plain directory. One package owns
    var/lock -> ../run/lock, the shape the base files ship; another owns one
    healthy file."""

    def setUp(self):
        super().setUp()
        (self.root / "run").mkdir()
        self._link("var/lock", "../run/lock")
        self._own("w18", ["var/lock"])
        self._file("usr/share/w18ok/f")
        self._own("w18ok", ["usr/share/w18ok/f"])

    def test_each_link_that_could_not_be_followed_is_named_with_its_reason(self):
        for args in (["verify", "--all"], ["verify", "--all", "--detail"],
                     ["verify", "--fast", "--all", "--detail"]):
            with self.subTest(args=args):
                rc, out = self._pkm(*args)
                self.assertEqual(rc, 3, out)
                self.assertIn("/var/lock -> ../run/lock", out)
                self.assertIn("not mounted under this root", out)

    def test_root_is_not_offered_for_a_link_root_cannot_follow_either(self):
        for args in (["verify", "--all"], ["verify", "--all", "--detail"]):
            with self.subTest(args=args):
                rc, out = self._pkm(*args)
                self.assertEqual(rc, 3, out)
                self.assertNotIn("Re-run as root", out,
                                 "the remedy printed does not apply to a "
                                 "runtime directory an offline root lacks")

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_root_is_offered_when_a_file_this_user_cannot_read_is_unknown(self):
        secret = self._file("usr/share/w18f/secret")
        self._own("w18f", ["usr/share/w18f/secret"])
        secret.chmod(0o000)
        self._locked.append(secret)
        rc, out = self._pkm("verify", "--all")
        self.assertEqual(rc, 3, out)
        self.assertIn("Re-run as root", out)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_detail_lists_the_path_behind_every_count(self):
        secret = self._file("usr/share/w18f/secret")
        self._own("w18f", ["usr/share/w18f/secret"])
        secret.chmod(0o000)
        self._locked.append(secret)
        # A regular file whose row carries no hash: registered before the
        # file exists, so nothing could be computed for it.
        self._own("w18n", ["usr/share/w18n/nohash"])
        self._file("usr/share/w18n/nohash")
        rc, out = self._pkm("verify", "--all", "--detail")
        self.assertEqual(rc, 3, out)
        self.assertIn("/usr/share/w18f/secret", out,
                      "--detail does not name the file it could not read")
        self.assertIn("/usr/share/w18n/nohash", out,
                      "--detail does not name the file with no recorded hash")
        self.assertIn("/var/lock -> ../run/lock", out)


class StrictModeReadsALinksBytesInsideTheRoot(_Root):
    """W14 and W14b: an owned link whose text is absolute. The same absolute
    path exists on the machine running the check with other bytes."""

    ROOT_BYTES = b"root-side\n"
    HOST_BYTES = b"host-side\n"

    def setUp(self):
        super().setUp()
        self.host_file = self.top / "hostside" / "name"
        self.host_file.parent.mkdir()
        self.host_file.write_bytes(self.HOST_BYTES)
        text = str(self.host_file)            # absolute
        self.in_root = self._file(text.lstrip("/"), self.ROOT_BYTES)
        self._link("usr/share/w14/name", text)

    def test_an_intact_root_is_not_reported_modified(self):
        self._own("w14", ["usr/share/w14/name"],
                  hashes={"usr/share/w14/name": _sha(self.ROOT_BYTES)})
        rc, out = self._pkm("verify", "--strict", "w14")
        self.assertEqual(rc, 0, out)
        self.assertIn("ok", out)

    def test_changed_bytes_in_the_root_are_reported_modified(self):
        self.in_root.write_bytes(b"changed!!\n")
        self._own("w14b", ["usr/share/w14/name"],
                  hashes={"usr/share/w14/name": _sha(self.HOST_BYTES)})
        rc, out = self._pkm("verify", "--strict", "w14b")
        self.assertEqual(rc, 1, out)
        self.assertIn("/usr/share/w14/name", out)
        self.assertIn("modified", out)


class AContentReadNeverWaits(_Root):
    """W5b: an owned link to a FIFO, its row carrying a hash, as most owned
    links' rows do. W5c: an owned regular file replaced by a FIFO."""

    def test_a_link_to_a_fifo_with_a_hash_is_reported_modified(self):
        d = self.root / "usr/share/w5b"
        d.mkdir(parents=True)
        os.mkfifo(d / "fifo")
        self._link("usr/share/w5b/l", "fifo")
        self._own("w5b", ["usr/share/w5b/l"],
                  hashes={"usr/share/w5b/l": _sha(b"payload\n")})
        rc, out = self._pkm("verify", "--strict", "w5b")
        self.assertEqual(rc, 1, out)
        self.assertIn("/usr/share/w5b/l", out)
        rc, out = self._pkm("verify", "--fast", "w5b")
        self.assertEqual(rc, 0, out)

    def test_a_regular_file_replaced_by_a_fifo_is_reported_modified(self):
        f = self._file("usr/share/w5c/f")
        self._own("w5c", ["usr/share/w5c/f"])
        f.unlink()
        os.mkfifo(f)
        rc, out = self._pkm("verify", "--strict", "w5c")
        self.assertEqual(rc, 1, out)
        self.assertIn("/usr/share/w5c/f", out)
        self.assertIn("modified", out)


if __name__ == "__main__":
    unittest.main()

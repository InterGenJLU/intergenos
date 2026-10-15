# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A read-only open that cannot read the archive a row needs says so, once.

On an installed system the package cache is root-only (/var/cache/pkm/packages,
mode 0700). A read-only command run by a person (pkm list, pkm info) or by the
notifier's unprivileged upgrade --all --dry-run cannot read the archive that
would confirm a recorded version carrying its own release, so the row reads as
recorded and its newer build is not listed. That used to happen with nothing
said, and the rule's reason called the archive "not on disk" although it was
there. Now the rule tells an archive this user cannot read apart from one that
is gone, and the open says so once, at the default level, naming the row and
the command that records the repair. Nothing is written. A gone archive says
nothing at the default level, and a readable archive (the root path) reads as
before.
"""
from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pkm import output
from pkm.database import PackageDB, release_suffix_verdict


def _make_archive(path, name, version, release):
    """An archive whose .PKGINFO states name, version and release."""
    pkginfo = (f"pkgname = {name}\npkgver = {version}\npkgrel = {release}\n"
               f"pkgdesc = test package\n").encode()
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(pkginfo)
        tf.addfile(info, io.BytesIO(pkginfo))
    return str(path)


SAID = "this user cannot read the archive"


@unittest.skipIf(os.geteuid() == 0,
                 "root reads a directory of mode 0000, so no archive is unreadable here")
class UnreadableArchiveIsSaidOnce(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pkm-unreadable-archive-")
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "pkm.db"
        self.cache = self.tmp / "packages"
        self.cache.mkdir()
        self._level = output.process_level()
        output.set_process_level(output.NORMAL)

    def tearDown(self):
        os.chmod(self.cache, 0o700)
        output.set_process_level(self._level)
        self._tmp.cleanup()

    def _seed(self, rows):
        """Rows as release 73 left them: '<version>-<release>' beside the release."""
        db = PackageDB(self.db_path, root=str(self.tmp))
        for name, version, release in rows:
            archive = _make_archive(self.cache / f"{name}-{version}-{release}.igos.tar.gz",
                                    name, version, release)
            db.add_installed(name=name, version=f"{version}-{release}",
                             release=release, archive_path=archive)
        db.close()

    def _read_only_reads(self):
        """One read-only open: get_installed and list_installed, as info and list do."""
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            db = PackageDB(self.db_path, root=str(self.tmp), read_only=True)
            try:
                got = {p["name"]: p["version"] for p in db.list_installed()}
                for name in list(got):
                    got[name] = db.get_installed(name)["version"]
                db.list_installed()
            finally:
                db.close()
        return got, err.getvalue()

    def test_an_unreadable_archive_is_said_once_per_open_naming_the_row(self):
        self._seed([("alpha", "1.0", 3), ("beta", "2.0", 5)])
        os.chmod(self.cache, 0o000)
        before = self.db_path.read_bytes()
        got, err = self._read_only_reads()
        self.assertEqual(got, {"alpha": "1.0-3", "beta": "2.0-5"})
        self.assertEqual(err.count(SAID), 1, err)
        self.assertIn("alpha's recorded version '1.0-3'", err)
        self.assertIn("sudo pkm update", err)
        self.assertEqual(self.db_path.read_bytes(), before)
        # A second open says it again, once: the line is per open, not per process.
        _, err2 = self._read_only_reads()
        self.assertEqual(err2.count(SAID), 1, err2)

    def test_the_verdict_tells_an_unreadable_archive_from_a_gone_one(self):
        self._seed([("alpha", "1.0", 3)])
        archive = str(self.cache / "alpha-1.0-3.igos.tar.gz")
        os.chmod(self.cache, 0o000)
        stem, why, unreadable = release_suffix_verdict("alpha", "1.0-3", 3, archive)
        self.assertEqual((stem, unreadable), (None, True))
        self.assertIn("cannot be read by this user", why)
        gone = str(self.tmp / "elsewhere" / "alpha-1.0-3.igos.tar.gz")
        stem, why, unreadable = release_suffix_verdict("alpha", "1.0-3", 3, gone)
        self.assertEqual((stem, unreadable), (None, False))
        self.assertIn("is not on disk", why)

    def test_a_gone_archive_says_nothing_at_the_default_level(self):
        self._seed([("alpha", "1.0", 3)])
        os.remove(self.cache / "alpha-1.0-3.igos.tar.gz")
        got, err = self._read_only_reads()
        self.assertEqual(got, {"alpha": "1.0-3"})
        self.assertEqual(err, "")

    def test_the_root_path_is_unchanged(self):
        # A reader who can read the archive, as root reads the cache: the
        # version the archive states, and nothing said at the default level.
        self._seed([("alpha", "1.0", 3)])
        got, err = self._read_only_reads()
        self.assertEqual(got, {"alpha": "1.0"})
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()

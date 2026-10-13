# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The before-remove check keys an archive member where the deploy lands it.

`pkm upgrade` and `pkm reinstall` remove the installed release and then
install the new archive, so they first ask refusal_before_replacing() whether
the new archive would put a different kind of file where another installed
package owns one (tests/pkm/test_upgrade_and_reinstall_refuse_before_the_remove.py).
That question is read from the archive's headers. The extract accepts one
member under several spellings and lands each at one path: ./usr/bin/../bin/y,
./usr//bin/./y and /usr/bin/y all land at usr/bin/y, and so does
./bin/../bin/y on a root whose /bin links to usr/bin. The header read kept the
spelling, found no owner at it, and the remove went ahead; install() then
refused against the path its staged tree holds, and with no pre-upgrade copy
the package stayed removed.

One function now names the path a member lands at (pkm/installer.py,
_member_target_path). The header read keys members by it, and both extracts
rename each member to it before anything is written, so the deploy's own
exclusions meet it as well. A member whose path climbs out of the root under
that reading is refused. These tests run the real installer, remover and
command paths on a scratch root with no pre-upgrade copy, and compare the
root's tree and the package database before and after.
"""
import argparse
import hashlib
import io
import os
import stat
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm import output
from pkm.database import PackageDB
from pkm.installer import (PackageInstaller, _archive_kinds,
                           _safe_extract_tar, _staged_kinds)
from pkm.remover import PackageRemover

ALPHA_TOOL = b"#!/bin/sh\necho alpha\n"

# One member, four spellings the extract lands at usr/bin/y on a root whose
# /bin and /lib link into /usr.
SPELLINGS = (
    "./usr/bin/../bin/y",
    "./usr//bin/./y",
    "/usr/bin/y",
    "./bin/../bin/y",
)

REFUSAL = "/usr/bin/y is a regular file owned by alpha"


def _archive(path, name, release, entries):
    """A minimal .igos.tar.gz: .PKGINFO, then each (member name, kind, bytes
    or link target) in the order given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    info = (f"pkgname={name}\npkgver=1.0\npkgrel={release}\npkgdesc=test\n"
            f"license=GPL\ntier=extra\nbuilddate=2026-10-13T00:00:00Z\n").encode()
    with tarfile.open(path, "w:gz") as tf:
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(info)
        tf.addfile(ti, io.BytesIO(info))
        for member, kind, payload in entries:
            ti = tarfile.TarInfo(member)
            if kind == "link":
                ti.type = tarfile.SYMTYPE
                ti.linkname = payload
                ti.mode = 0o777
                tf.addfile(ti)
            elif kind == "dir":
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                tf.addfile(ti)
            else:
                ti.mode = 0o644
                ti.size = len(payload)
                tf.addfile(ti, io.BytesIO(payload))
    return path


def _tree(root):
    """Every entry under ``root``: its kind, and its bytes and mode, its
    mode, or its link target."""
    seen = {}
    for here, dirs, files in os.walk(root):
        for entry in dirs + files:
            full = os.path.join(here, entry)
            st = os.lstat(full)
            rel = os.path.relpath(full, root)
            if stat.S_ISLNK(st.st_mode):
                seen[rel] = ("link", os.readlink(full))
            elif stat.S_ISDIR(st.st_mode):
                seen[rel] = ("dir", stat.S_IMODE(st.st_mode))
            else:
                seen[rel] = ("file", Path(full).read_bytes(),
                             stat.S_IMODE(st.st_mode))
    return seen


def _rows(db):
    """The installed packages and every path each one owns."""
    installed = [tuple(r) for r in db.conn.execute(
        "SELECT name, version, release FROM installed ORDER BY name")]
    files = [tuple(r) for r in db.conn.execute(
        "SELECT i.name, f.path, f.is_dir FROM files f "
        "JOIN installed i ON f.package_id = i.id ORDER BY i.name, f.path")]
    return installed, files


def _usrmerge_root(root):
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "lib").mkdir(parents=True)
    os.symlink("usr/bin", root / "bin")
    os.symlink("usr/lib", root / "lib")


class _RepoWith:
    def __init__(self, entries=None):
        self.entries = entries or {}

    def get_package(self, name):
        return self.entries.get(name)

    def download_package(self, name, reporter=None):
        raise AssertionError("nothing is downloaded in these tests")

    def resolve_dependencies(self, name, db):
        return True, [name]


class _Scratch:
    """A root whose /bin and /lib link into /usr: alpha owns a regular file at
    usr/bin/y and widget 1.0-3 is installed, both from local archives."""

    def __init__(self, base):
        self.tmp = Path(tempfile.mkdtemp(dir=base))
        self.root = self.tmp / "root"
        _usrmerge_root(self.root)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))
        for name, release, entries in (
                ("alpha", 1, [("./usr/bin/y", "file", ALPHA_TOOL)]),
                ("widget", 3, [("./usr/bin/widget", "file", b"three\n")])):
            archive = _archive(
                self.tmp / "installed" / f"{name}-1.0-{release}.igos.tar.gz",
                name, release, entries)
            ok, msg = self.installer.install(name, archive_path=str(archive))
            if not ok:
                raise AssertionError(f"setting up {name}: {msg}")

    def state(self):
        return _tree(self.root), _rows(self.db)

    def close(self):
        self.db.close()


class _Case(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def scratch(self):
        s = _Scratch(self.base)
        self.addCleanup(s.close)
        return s

    def _run(self, call):
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(output.NORMAL)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                try:
                    rc = call()
                except SystemExit as e:
                    rc = e.code
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        return rc, " ".join(buf.getvalue().split())


class TheHeaderReadKeysEverySpellingAsTheExtractLandsIt(unittest.TestCase):

    def test_header_kinds_equal_staged_kinds(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "root"
            _usrmerge_root(root)
            entries = [("./usr/bin/widget", "file", b"four\n")]
            entries += [(f"{spelling}{i}", "link", "widget")
                        for i, spelling in enumerate(SPELLINGS)]
            archive = _archive(tmp / "widget-1.0-4.igos.tar.gz", "widget", 4,
                               entries)
            staging = tmp / "staging"
            staging.mkdir()
            ok, msg = _safe_extract_tar(archive, staging, usrmerge_root=root)
            self.assertTrue(ok, msg)

            header_kinds, _ = _archive_kinds(archive, root, "widget")

            expected = {"usr/bin/widget": "file"}
            expected.update({f"usr/bin/y{i}": "link"
                             for i in range(len(SPELLINGS))})
            self.assertEqual(_staged_kinds(staging), expected)
            self.assertEqual(header_kinds, expected)


class AnInstallRefusesEverySpellingBeforeWriting(_Case):

    def test_each_spelling_is_refused_and_nothing_changes(self):
        for spelling in SPELLINGS:
            with self.subTest(spelling=spelling):
                s = self.scratch()
                offered = _archive(s.tmp / "offered" / "gamma-1.0-1.igos.tar.gz",
                                   "gamma", 1,
                                   [("./usr/bin/gamma", "file", b"gamma\n"),
                                    (spelling, "link", "gamma")])
                before = s.state()
                ok, msg = s.installer.install("gamma", archive_path=str(offered))
                said = " ".join(msg.split())
                self.assertFalse(ok, said)
                self.assertIn(REFUSAL, said)
                self.assertEqual(s.state(), before)


class AnUpgradeRefusesEverySpellingBeforeTheRemove(_Case):

    def test_each_spelling_is_refused_and_nothing_changes(self):
        for spelling in SPELLINGS:
            with self.subTest(spelling=spelling):
                s = self.scratch()
                offered = _archive(s.tmp / "offered" / "widget-1.0-4.igos.tar.gz",
                                   "widget", 4,
                                   [("./usr/bin/widget", "file", b"four\n"),
                                    (spelling, "link", "widget")])
                args = argparse.Namespace(
                    packages=["widget"], upgrade_all=False, allow_downgrade=False,
                    ignore_holds=False, upgrade_security_only=False,
                    upgrade_allow_kernel_replace=False, upgrade_yes=True,
                    upgrade_dry_run=False, quiet=False, verbose=False,
                    archive=str(offered), archive_trust="loose")
                before = s.state()
                with patch.object(cli, "RepoManager", lambda *a, **k: _RepoWith()), \
                     patch.object(cli, "PackageInstaller",
                                  lambda *a, **k: s.installer), \
                     patch("pkm.remover.PackageRemover",
                           lambda db, root=None: PackageRemover(db, root=str(s.root))), \
                     patch.object(cli, "_save_rollback_archive", lambda *a, **k: None), \
                     patch.object(cli, "refresh_available_updates_after_transaction",
                                  lambda db, **k: None), \
                     patch.object(cli, "_print_transaction_next_steps",
                                  lambda *a, **k: None), \
                     patch.object(cli, "helper_is_present", lambda n: False), \
                     patch.object(cli, "helper_payload_present", lambda n: False), \
                     patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                    rc, said = self._run(lambda: cli.cmd_upgrade(s.db, args))
                self.assertNotEqual(rc, 0, said)
                self.assertIn(REFUSAL, said)
                self.assertIn("Nothing was removed", said)
                self.assertEqual(s.state(), before, said)


class AReinstallRefusesEverySpellingBeforeTheRemove(_Case):

    def test_each_spelling_is_refused_and_nothing_changes(self):
        for spelling in SPELLINGS:
            with self.subTest(spelling=spelling):
                s = self.scratch()
                offered = _archive(s.tmp / "cache" / "widget-1.0-3.igos.tar.gz",
                                   "widget", 3,
                                   [("./usr/bin/widget", "file", b"three\n"),
                                    (spelling, "link", "widget")])
                sha = hashlib.sha256(offered.read_bytes()).hexdigest()
                repo = _RepoWith({"widget": {"name": "widget", "version": "1.0",
                                             "release": 3, "sha256": sha}})
                before = s.state()
                with patch.object(cli, "package_installer", lambda db: s.installer), \
                     patch.object(cli, "PackageRemover",
                                  lambda db: PackageRemover(db, root=str(s.root))), \
                     patch.object(cli, "repo_manager", lambda: repo), \
                     patch.object(s.installer, "_find_archive", lambda name: offered), \
                     patch.object(cli, "helper_is_present", lambda n: False), \
                     patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                    rc, said = self._run(lambda: cli.cmd_reinstall(
                        s.db, cli.build_parser().parse_args(["reinstall", "widget"])))
                self.assertNotEqual(rc, 0, said)
                self.assertIn(REFUSAL, said)
                self.assertIn("still installed", said)
                self.assertEqual(s.state(), before, said)


class TheDeploysExclusionMeetsEverySpelling(_Case):
    """A configuration file the person changed is left out of the deploy and
    its shipped copy written beside it, whatever spelling carries it."""

    def test_the_persons_file_survives_each_spelling(self):
        for spelling in ("./etc//demo.conf", "./etc/./demo.conf",
                         "./usr/../etc/demo.conf", "/etc/demo.conf"):
            with self.subTest(spelling=spelling):
                tmp = Path(tempfile.mkdtemp(dir=self.base))
                root = tmp / "root"
                _usrmerge_root(root)
                (root / "etc").mkdir()
                (root / "etc" / "demo.conf").write_bytes(b"the person's own\n")
                db = PackageDB(tmp / "pkm.db", root=str(root))
                self.addCleanup(db.close)
                installer = PackageInstaller(db, root=str(root))
                offered = _archive(tmp / "cfg-1.0-1.igos.tar.gz", "cfg", 1,
                                   [(spelling, "file", b"as shipped\n")])
                ok, msg = installer.install("cfg", archive_path=str(offered))
                self.assertTrue(ok, msg)
                self.assertEqual((root / "etc" / "demo.conf").read_bytes(),
                                 b"the person's own\n")
                self.assertEqual((root / "etc" / "demo.conf.pkmnew").read_bytes(),
                                 b"as shipped\n")


class AMemberThatClimbsOutOfTheRootIsRefused(_Case):

    def test_each_spelling_that_climbs_out_is_refused(self):
        for spelling in ("../escape", "./usr/../../escape", "/../escape",
                         "./usr/bin/../../../escape"):
            with self.subTest(spelling=spelling):
                tmp = Path(tempfile.mkdtemp(dir=self.base))
                dest = tmp / "dest"
                dest.mkdir()
                archive = _archive(tmp / "w-1.0-1.igos.tar.gz", "w", 1,
                                   [("./usr/bin/w", "file", b"w\n"),
                                    (spelling, "file", b"out\n")])
                ok, msg = _safe_extract_tar(archive, dest)
                self.assertFalse(ok, msg)
                self.assertIn("outside the destination", msg)
                self.assertFalse((tmp / "escape").exists())

    def test_climbing_out_through_a_link_the_archive_ships_is_refused(self):
        # a/b links to c/d/e, so a/b/../../../x resolves on disk to a/x,
        # inside the root; as written it climbs out of it, and it is refused.
        tmp = Path(tempfile.mkdtemp(dir=self.base))
        dest = tmp / "dest"
        dest.mkdir()
        archive = _archive(tmp / "w-1.0-1.igos.tar.gz", "w", 1,
                           [("./a/c/d/e/", "dir", None),
                            ("./a/b", "link", "c/d/e"),
                            ("./a/b/../../../x", "file", b"x\n")])
        ok, msg = _safe_extract_tar(archive, dest)
        self.assertFalse(ok, msg)
        self.assertIn("outside the destination", msg)
        self.assertFalse((dest / "a" / "x").exists())
        self.assertFalse((tmp / "x").exists())


if __name__ == "__main__":
    unittest.main()

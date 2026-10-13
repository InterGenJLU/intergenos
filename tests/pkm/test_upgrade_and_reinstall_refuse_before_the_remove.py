# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An upgrade or a reinstall refuses a kind change BEFORE it removes anything.

`pkm upgrade` and `pkm reinstall` remove the installed release first and then
install the new archive. install() refuses an archive that would put a
different kind of file where another installed package owns one
(tests/pkm/test_install_refuses_a_kind_change_at_another_packages_path.py),
but on these two paths it ran only after the remove: the rollback then
reinstalled the old release from its pre-upgrade copy, and where no copy
existed — a package installed from an uncached local archive, or after
`pkm cache clean` — the package stayed removed.

Both commands now ask the same question of the new archive first, reading its
headers through the deploy's own UsrMerge remap without extracting anything,
and refuse while the installed release is still untouched. These tests run the
real installer and remover on a scratch root, with no rollback copy.
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
from pkm.installer import PackageInstaller
from pkm.remover import PackageRemover

ALPHA_TOOL = b"#!/bin/sh\necho alpha\n"


def _archive(path, name, release, files=None, links=None):
    """A minimal .igos.tar.gz: .PKGINFO, regular files and symbolic links."""
    files = files or {}
    links = links or {}
    info = (f"pkgname={name}\npkgver=1.0\npkgrel={release}\npkgdesc=test\n"
            f"license=GPL\ntier=extra\nbuilddate=2026-10-13T00:00:00Z\n").encode()
    with tarfile.open(path, "w:gz") as tf:
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(info)
        tf.addfile(ti, io.BytesIO(info))
        for rel, data in files.items():
            ti = tarfile.TarInfo(f"./{rel}")
            ti.mode = 0o755
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        for rel, target in links.items():
            ti = tarfile.TarInfo(f"./{rel}")
            ti.type = tarfile.SYMTYPE
            ti.linkname = target
            ti.mode = 0o777
            tf.addfile(ti)
    return path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _RepoWith:
    def __init__(self, entries=None):
        self.entries = entries or {}

    def get_package(self, name):
        return self.entries.get(name)

    def download_package(self, name, reporter=None):
        raise AssertionError("nothing is downloaded in these tests")

    def resolve_dependencies(self, name, db):
        return True, [name]


class _ScratchRoot(unittest.TestCase):
    """alpha owns a regular file at usr/bin/y; widget 1.0-3 is installed; the
    archive offered for widget puts a symbolic link at usr/bin/y."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        (self.root / "usr" / "bin").mkdir(parents=True)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))
        alpha = _archive(self.tmp / "alpha-1.0-1.igos.tar.gz", "alpha", 1,
                         files={"usr/bin/y": ALPHA_TOOL})
        ok, msg = self.installer.install("alpha", archive_path=str(alpha))
        self.assertTrue(ok, msg)
        widget = _archive(self.tmp / "widget-1.0-3.igos.tar.gz", "widget", 3,
                          files={"usr/bin/widget": b"three\n"})
        ok, msg = self.installer.install("widget", archive_path=str(widget))
        self.assertTrue(ok, msg)

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _assert_nothing_changed(self, said):
        self.assertIn("/usr/bin/y", said)
        self.assertIn("alpha", said)
        row = self.db.get_installed("widget")
        self.assertIsNotNone(row, f"widget was removed: {said}")
        self.assertEqual(int(row["release"]), 3)
        self.assertEqual((self.root / "usr" / "bin" / "widget").read_bytes(),
                         b"three\n")
        landed = os.lstat(self.root / "usr" / "bin" / "y")
        self.assertTrue(stat.S_ISREG(landed.st_mode))
        self.assertEqual((self.root / "usr" / "bin" / "y").read_bytes(), ALPHA_TOOL)

    def _capture(self):
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(output.NORMAL)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        return buf, prior

    def _release(self, prior):
        output.set_process_level(prior)
        output._process_reporter.stream = None
        output._process_reporter.err_stream = None


class AnUpgradeRefusesBeforeTheRemove(_ScratchRoot):

    def test_the_installed_release_is_untouched(self):
        new = _archive(self.tmp / "widget-1.0-4.igos.tar.gz", "widget", 4,
                       files={"usr/bin/widget": b"four\n"},
                       links={"usr/bin/y": "widget"})
        args = argparse.Namespace(
            packages=["widget"], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=False, upgrade_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
            archive=str(new), archive_trust="loose")
        buf, prior = self._capture()
        try:
            with redirect_stdout(buf), redirect_stderr(buf), \
                 patch.object(cli, "RepoManager", lambda *a, **k: _RepoWith()), \
                 patch.object(cli, "PackageInstaller", lambda *a, **k: self.installer), \
                 patch("pkm.remover.PackageRemover",
                       lambda db, root=None: PackageRemover(db, root=str(self.root))), \
                 patch.object(cli, "_save_rollback_archive", lambda *a, **k: None), \
                 patch.object(cli, "refresh_available_updates_after_transaction",
                              lambda db, **k: None), \
                 patch.object(cli, "_print_transaction_next_steps", lambda *a, **k: None), \
                 patch.object(cli, "helper_is_present", lambda n: False), \
                 patch.object(cli, "helper_payload_present", lambda n: False), \
                 patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                try:
                    rc = cli.cmd_upgrade(self.db, args)
                except SystemExit as e:
                    rc = e.code
        finally:
            self._release(prior)
        said = " ".join(buf.getvalue().split())
        self.assertNotEqual(rc, 0, said)
        self.assertIn("Nothing was removed", said)
        self._assert_nothing_changed(said)


class AReinstallRefusesBeforeTheRemove(_ScratchRoot):

    def test_the_installed_release_is_untouched(self):
        (self.tmp / "cache").mkdir()
        offered = _archive(self.tmp / "cache" / "widget-1.0-3.igos.tar.gz",
                           "widget", 3,
                           files={"usr/bin/widget": b"three\n"},
                           links={"usr/bin/y": "widget"})
        repo = _RepoWith({"widget": {"name": "widget", "version": "1.0",
                                     "release": 3, "sha256": _sha(offered)}})
        buf, prior = self._capture()
        try:
            with redirect_stdout(buf), redirect_stderr(buf), \
                 patch.object(cli, "package_installer", lambda db: self.installer), \
                 patch.object(cli, "PackageRemover",
                              lambda db: PackageRemover(db, root=str(self.root))), \
                 patch.object(cli, "repo_manager", lambda: repo), \
                 patch.object(self.installer, "_find_archive", lambda name: offered), \
                 patch.object(cli, "helper_is_present", lambda n: False), \
                 patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                try:
                    rc = cli.cmd_reinstall(
                        self.db, cli.build_parser().parse_args(["reinstall", "widget"]))
                except SystemExit as e:
                    rc = e.code
        finally:
            self._release(prior)
        said = " ".join(buf.getvalue().split())
        self.assertNotEqual(rc, 0, said)
        self.assertIn("still installed", said)
        self._assert_nothing_changed(said)


class TheHeaderReadMapsPathsAsTheDeployDoes(unittest.TestCase):
    """The pre-check reads headers, install() reads its staged tree; the two
    must name the same paths with the same kinds, UsrMerge remap included,
    or the pre-check would compare paths the deploy never writes."""

    def test_header_kinds_equal_staged_kinds_on_a_usrmerge_root(self):
        from pkm.installer import (_archive_kinds, _safe_extract_tar,
                                   _staged_kinds)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "root"
            (root / "usr" / "bin").mkdir(parents=True)
            (root / "usr" / "lib").mkdir(parents=True)
            os.symlink("usr/bin", root / "bin")
            os.symlink("usr/lib", root / "lib")
            archive = tmp / "demo-1.0-1.igos.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                for rel, data in (("./.PKGINFO", b"pkgname=demo\n"),
                                  ("./bin/tool", b"tool\n"),
                                  ("./lib/libdemo.so.1", b"elf\n"),
                                  ("./usr/share/doc/demo/README", b"doc\n"),
                                  ("./var/lib/igos/packages/demo-1.0",
                                   b"SUPERSEDES: old-demo\nFILE LIST:\n")):
                    ti = tarfile.TarInfo(rel)
                    ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))
                for rel, target in (("./lib/libdemo.so", "libdemo.so.1"),
                                    ("./usr/share/demo", "doc/demo")):
                    ti = tarfile.TarInfo(rel)
                    ti.type = tarfile.SYMTYPE
                    ti.linkname = target
                    tf.addfile(ti)
                ti = tarfile.TarInfo("./bin/tool-again")
                ti.type = tarfile.LNKTYPE
                ti.linkname = "./bin/tool"
                tf.addfile(ti)
            staging = tmp / "staging"
            staging.mkdir()
            ok, msg = _safe_extract_tar(archive, staging, usrmerge_root=root)
            self.assertTrue(ok, msg)

            header_kinds, supersedes = _archive_kinds(archive, root, "demo")

            self.assertEqual(header_kinds, _staged_kinds(staging))
            self.assertEqual(header_kinds["usr/bin/tool"], "file")
            self.assertEqual(header_kinds["usr/bin/tool-again"], "file")
            self.assertEqual(header_kinds["usr/lib/libdemo.so"], "link")
            self.assertEqual(header_kinds["usr/share/demo"], "link")
            self.assertEqual(supersedes, ["old-demo"])


if __name__ == "__main__":
    unittest.main()

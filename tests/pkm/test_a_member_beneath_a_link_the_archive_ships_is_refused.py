# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A member beneath a symbolic link the same archive ships is refused.

An archive carrying the link usr/lib64 -> lib and then a member usr/lib64/y
has the extract write y THROUGH the link, at usr/lib/y, a path no member
names. The check `pkm upgrade` and `pkm reinstall` make before they remove the
installed release reads the archive's headers, where the link does not exist
yet, so it keyed usr/lib64/y; install()'s staged tree holds usr/lib/y. Where
another installed package owns usr/lib/y as a regular file, the check found
no owner, the remove went ahead, and install() refused only afterwards
(measured on a scratch root, 2026-10-13).

A pkm-built archive never carries such a member: tar archives a link to a
directory as the link and does not descend into it. So the extract refuses
the archive before anything is written, and the before-remove check refuses
it too, both naming the member and the link. This test runs the real
installer, remover and upgrade path on a scratch root with no pre-upgrade
copy, and compares the root's tree and the package database before and after.
"""
import argparse
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
from pkm.installer import PackageInstaller, _safe_extract_tar
from pkm.remover import PackageRemover

NAMED = "/usr/lib64/y beneath the link /usr/lib64"


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
            else:
                ti.mode = 0o644
                ti.size = len(payload)
                tf.addfile(ti, io.BytesIO(payload))
    return path


def _state(root, db):
    """Every entry under ``root`` with its kind and content, and the
    installed packages with every path each owns."""
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
    installed = [tuple(r) for r in db.conn.execute(
        "SELECT name, version, release FROM installed ORDER BY name")]
    files = [tuple(r) for r in db.conn.execute(
        "SELECT i.name, f.path, f.is_dir FROM files f "
        "JOIN installed i ON f.package_id = i.id ORDER BY i.name, f.path")]
    return seen, installed, files


class _EmptyRepo:
    def get_package(self, name):
        return None

    def resolve_dependencies(self, name, db):
        return True, [name]


class AMemberBeneathALinkTheArchiveShipsIsRefused(unittest.TestCase):

    def test_the_upgrade_and_the_extract_refuse_it_and_nothing_changes(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "root"
            (root / "usr" / "bin").mkdir(parents=True)
            (root / "usr" / "lib").mkdir(parents=True)
            os.symlink("usr/bin", root / "bin")
            os.symlink("usr/lib", root / "lib")
            db = PackageDB(tmp / "pkm.db", root=str(root))
            self.addCleanup(db.close)
            installer = PackageInstaller(db, root=str(root))
            for name, release, entries in (
                    ("alpha", 1, [("./usr/lib/y", "file", b"alpha\n")]),
                    ("widget", 3, [("./usr/bin/widget", "file", b"three\n")])):
                archive = _archive(
                    tmp / "installed" / f"{name}-1.0-{release}.igos.tar.gz",
                    name, release, entries)
                ok, msg = installer.install(name, archive_path=str(archive))
                self.assertTrue(ok, msg)
            offered = _archive(tmp / "offered" / "widget-1.0-4.igos.tar.gz",
                               "widget", 4,
                               [("./usr/bin/widget", "file", b"four\n"),
                                ("./usr/lib/widget-data", "file", b"data\n"),
                                ("./usr/lib64", "link", "lib"),
                                ("./usr/lib64/y", "link", "widget")])
            args = argparse.Namespace(
                packages=["widget"], upgrade_all=False, allow_downgrade=False,
                ignore_holds=False, upgrade_security_only=False,
                upgrade_allow_kernel_replace=False, upgrade_yes=True,
                upgrade_dry_run=False, quiet=False, verbose=False,
                archive=str(offered), archive_trust="loose")
            before = _state(root, db)
            buf = io.StringIO()
            prior = output.process_level()
            output.set_process_level(output.NORMAL)
            output._process_reporter.stream = buf
            output._process_reporter.err_stream = buf
            try:
                with redirect_stdout(buf), redirect_stderr(buf), \
                     patch.object(cli, "RepoManager", lambda *a, **k: _EmptyRepo()), \
                     patch.object(cli, "PackageInstaller", lambda *a, **k: installer), \
                     patch("pkm.remover.PackageRemover",
                           lambda db, root=None: PackageRemover(db, root=str(root))), \
                     patch.object(cli, "_save_rollback_archive", lambda *a, **k: None), \
                     patch.object(cli, "refresh_available_updates_after_transaction",
                                  lambda db, **k: None), \
                     patch.object(cli, "_print_transaction_next_steps",
                                  lambda *a, **k: None), \
                     patch.object(cli, "helper_is_present", lambda n: False), \
                     patch.object(cli, "helper_payload_present", lambda n: False), \
                     patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                    try:
                        rc = cli.cmd_upgrade(db, args)
                    except SystemExit as e:
                        rc = e.code
            finally:
                output.set_process_level(prior)
                output._process_reporter.stream = None
                output._process_reporter.err_stream = None
            said = " ".join(buf.getvalue().split())
            self.assertNotEqual(rc, 0, said)
            self.assertIn(NAMED, said)
            self.assertIn("Nothing was removed", said)
            self.assertEqual(_state(root, db), before, said)

            dest = tmp / "dest"
            dest.mkdir()
            ok, msg = _safe_extract_tar(offered, dest)
            self.assertFalse(ok, msg)
            self.assertIn(NAMED, msg)
            self.assertEqual(list(dest.iterdir()), [], msg)


if __name__ == "__main__":
    unittest.main()

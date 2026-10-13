# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A hard-link entry is read, and written, as what it names.

The check `pkm upgrade` and `pkm reinstall` make before they remove the
installed release reads the new archive's headers, and it counted every
hard-link entry as a regular file. tarfile lands a hard link as what it names
among the members before it, and where that is the archive's own symbolic
link the result was not a regular file (measured 2026-10-13):

- an archive with ./a -> usr/bin, ./c a hard link naming ./a, and ./c/y:
  os.link followed ./a to a directory and failed, tarfile extracted a copy of
  the link at ./c, and ./c/y was written through it into usr/bin. The header
  read keyed c as a file and c/y beneath nothing, the before-remove check
  found no owner, and an upgrade removed the installed release before
  install() refused against usr/bin/y;
- the same entry over a path another package owns as a regular file: the
  header read saw a regular file there too, no change of kind, and the
  upgrade again removed the release before the refusal;
- tarfile then chmods the entry's path with the hard link's mode, which
  follows the new link and re-modes the directory it names;
- a hard link naming a directory lands as a directory, and one naming no
  member before it made tarfile raise KeyError mid-extract.

The extract now writes an entry naming a link as that link and one naming a
directory as a directory, the header read counts each the same way, a member
beneath such a link is refused by both, and so is a hard link that names
nothing. These tests run the real installer, remover and upgrade path on
scratch roots with no pre-upgrade copy.
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
from pkm.installer import (PackageInstaller, _archive_kinds,
                           _safe_extract_tar, _staged_kinds)
from pkm.remover import PackageRemover


def _archive(path, name, release, entries):
    """A minimal .igos.tar.gz: .PKGINFO, then each (member, kind, payload) in
    order; kind "file" takes bytes, "dir" nothing, "sym" a link target and
    "hard" the member it names."""
    path.parent.mkdir(parents=True, exist_ok=True)
    info = (f"pkgname={name}\npkgver=1.0\npkgrel={release}\npkgdesc=test\n"
            f"license=GPL\ntier=extra\nbuilddate=2026-10-13T00:00:00Z\n").encode()
    with tarfile.open(path, "w:gz") as tf:
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(info)
        tf.addfile(ti, io.BytesIO(info))
        for member, kind, payload in entries:
            ti = tarfile.TarInfo(member)
            if kind == "file":
                ti.mode = 0o755
                ti.size = len(payload)
                tf.addfile(ti, io.BytesIO(payload))
            elif kind == "dir":
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                tf.addfile(ti)
            else:
                ti.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                ti.linkname = payload
                ti.mode = 0o777 if kind == "sym" else 0o644
                tf.addfile(ti)
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


class _Case(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def upgrade(self, offered_entries, alpha_entries):
        """alpha installed from alpha_entries, widget 1.0-3 installed; then
        `pkm upgrade widget` from an archive of offered_entries, with no
        pre-upgrade copy. Returns (rc, output, state before, state after)."""
        tmp = Path(tempfile.mkdtemp(dir=self.base))
        root = tmp / "root"
        root.mkdir()
        db = PackageDB(tmp / "pkm.db", root=str(root))
        self.addCleanup(db.close)
        installer = PackageInstaller(db, root=str(root))
        for name, release, entries in (
                ("alpha", 1, alpha_entries),
                ("widget", 3, [("./usr/bin/widget", "file", b"three\n")])):
            archive = _archive(
                tmp / "installed" / f"{name}-1.0-{release}.igos.tar.gz",
                name, release, entries)
            ok, msg = installer.install(name, archive_path=str(archive))
            self.assertTrue(ok, msg)
        offered = _archive(tmp / "offered" / "widget-1.0-4.igos.tar.gz",
                           "widget", 4, offered_entries)
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
        return rc, " ".join(buf.getvalue().split()), before, _state(root, db)


ALPHA_Y = [("./usr/bin/y", "file", b"alpha\n")]


class AnUpgradeRefusesBeforeTheRemove(_Case):

    def test_a_member_beneath_a_shipped_link(self):
        rc, said, before, after = self.upgrade(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./a", "sym", "usr/bin"),
             ("./a/y", "sym", "widget")], ALPHA_Y)
        self.assertNotEqual(rc, 0, said)
        self.assertIn("/a/y beneath the link /a", said)
        self.assertIn("Nothing was removed", said)
        self.assertEqual(after, before, said)

    def test_a_member_beneath_a_hard_link_naming_the_shipped_link(self):
        rc, said, before, after = self.upgrade(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./a", "sym", "usr/bin"),
             ("./c", "hard", "./a"),
             ("./c/y", "sym", "widget")], ALPHA_Y)
        self.assertNotEqual(rc, 0, said)
        self.assertIn("/c/y beneath the link /c", said)
        self.assertIn("Nothing was removed", said)
        self.assertEqual(after, before, said)

    def test_a_hard_link_naming_the_shipped_link_over_another_packages_file(self):
        rc, said, before, after = self.upgrade(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./a", "sym", "usr/bin"),
             ("./c", "hard", "./a")],
            [("./c", "file", b"alpha's c\n")] + ALPHA_Y)
        self.assertNotEqual(rc, 0, said)
        self.assertIn("/c is a regular file owned by alpha", said)
        self.assertIn("Nothing was removed", said)
        self.assertEqual(after, before, said)

    def test_a_hard_link_naming_nothing(self):
        rc, said, before, after = self.upgrade(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./c", "hard", "./usr/bin/nothing")], ALPHA_Y)
        self.assertNotEqual(rc, 0, said)
        self.assertIn("/c, a hard link to ./usr/bin/nothing", said)
        self.assertIn("Nothing was removed", said)
        self.assertEqual(after, before, said)


class TheExtractWritesAHardLinkAsWhatItNames(_Case):

    def _extract(self, entries, dest=None):
        tmp = Path(tempfile.mkdtemp(dir=self.base))
        dest = dest or tmp / "dest"
        dest.mkdir(exist_ok=True)
        archive = _archive(tmp / "w-1.0-1.igos.tar.gz", "w", 1, entries)
        try:
            ok, msg = _safe_extract_tar(archive, dest)
        except Exception as e:  # the measured failure was a raise
            self.fail(f"the extract raised {type(e).__name__}: {e}")
        return archive, dest, ok, msg

    def test_a_member_beneath_it_is_not_written_over_another_packages_file(self):
        tmp = Path(tempfile.mkdtemp(dir=self.base))
        root = tmp / "root"
        (root / "usr" / "bin").mkdir(parents=True)
        (root / "usr" / "bin" / "y").write_bytes(b"alpha\n")
        _, _, ok, msg = self._extract(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./a", "sym", "usr/bin"),
             ("./c", "hard", "./a"),
             ("./c/y", "file", b"widget's y\n")], dest=root)
        self.assertFalse(ok, msg)
        self.assertIn("/c/y beneath the link /c", msg)
        self.assertEqual((root / "usr" / "bin" / "y").read_bytes(), b"alpha\n")
        self.assertFalse(os.path.lexists(root / "c"))

    def test_an_entry_naming_a_link_is_that_link_and_re_modes_nothing(self):
        archive, dest, ok, msg = self._extract(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./a", "sym", "usr/bin"),
             ("./c", "hard", "./a")])
        self.assertTrue(ok, msg)
        self.assertEqual(os.readlink(dest / "c"), "usr/bin")
        # usr and usr/bin were both made by the extract for usr/bin/widget.
        self.assertEqual(stat.S_IMODE(os.lstat(dest / "usr" / "bin").st_mode),
                         stat.S_IMODE(os.lstat(dest / "usr").st_mode))
        self.assertEqual(stat.S_IMODE(os.lstat(dest / "usr" / "bin" / "widget").st_mode),
                         0o755)
        self.assertEqual(_archive_kinds(archive, dest, "w")[0], _staged_kinds(dest))

    def test_an_entry_naming_a_link_to_a_file_is_that_link(self):
        archive, dest, ok, msg = self._extract(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./a", "sym", "usr/bin/widget"),
             ("./c", "hard", "./a")])
        self.assertTrue(ok, msg)
        self.assertEqual(os.readlink(dest / "c"), "usr/bin/widget")
        self.assertEqual(stat.S_IMODE(os.lstat(dest / "usr" / "bin" / "widget").st_mode),
                         0o755)
        self.assertEqual(_archive_kinds(archive, dest, "w")[0], _staged_kinds(dest))

    def test_an_entry_naming_a_directory_is_a_directory(self):
        archive, dest, ok, msg = self._extract(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./d/", "dir", None),
             ("./c", "hard", "./d")])
        self.assertTrue(ok, msg)
        self.assertTrue(stat.S_ISDIR(os.lstat(dest / "c").st_mode))
        self.assertEqual(_archive_kinds(archive, dest, "w")[0], _staged_kinds(dest))

    def test_an_entry_naming_a_file_is_still_a_second_name_for_it(self):
        archive, dest, ok, msg = self._extract(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./c", "hard", "./usr/bin/widget")])
        self.assertTrue(ok, msg)
        self.assertEqual(os.lstat(dest / "c").st_ino,
                         os.lstat(dest / "usr" / "bin" / "widget").st_ino)
        self.assertEqual(_archive_kinds(archive, dest, "w")[0], _staged_kinds(dest))

    def test_an_entry_naming_nothing_is_refused_before_anything_lands(self):
        _, dest, ok, msg = self._extract(
            [("./usr/bin/widget", "file", b"four\n"),
             ("./c", "hard", "./usr/bin/nothing")])
        self.assertFalse(ok, msg)
        self.assertIn("/c, a hard link to ./usr/bin/nothing", msg)
        self.assertEqual(list(dest.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

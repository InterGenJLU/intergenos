# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Equal version and release is not proof of the same build.

`pkm upgrade <name> --archive <file>` decided direction by version
comparison alone. When the numbers matched it printed "already installed at
exactly the archive's build. Nothing to do" and changed nothing — whatever
the archive actually held. Measured on an installed machine: an archive at
the same version and release as the installed package, carrying a function
twice that was absent on disk, was answered "nothing to do". A person who
has just built that archive is told the machine already has it.

WHAT IS COMPARED, and why it is not an archive hash. pkm records no
archive-level sha256 for an installed build anywhere: the installed row's
`manifest_sha256` is the hash of the TEXT MANIFEST's bytes, not of an
archive, and nothing else in the schema could stand in for one. What every
installed build DOES record is the sha256 of each file it deployed, in the
`files` table and in the text manifest beside it. That set is the recorded
identity of the installed build, and it is what the archive is compared
against.

Three outcomes, and only one of them lets the old sentence be printed:

  identical     -> "exactly the archive's build", nothing done (unchanged);
  different     -> the difference is named and the archive is deployed;
  unrecorded    -> the installed build records no content to compare, so
                   identity cannot be asserted; that is said plainly and
                   the archive is deployed.

Deploying is the fail-closed direction in both of the last two: the person
named this file and asked for it to be installed, and the alternative is a
machine that silently keeps bytes nobody established were the right ones.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm import output
from pkm.database import PackageDB

NAME = "widget"
PAYLOAD = {"usr/bin/widget": b"#!/bin/sh\necho three\n",
           "usr/share/widget/data": b"a\nb\nc\n"}


def _make_archive(path, name, version, release, files):
    pkginfo = (f"pkgname = {name}\npkgver = {version}\npkgrel = {release}\n"
               f"pkgdesc = test package\n")
    with tarfile.open(path, "w:gz") as tf:
        data = pkginfo.encode()
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        for rel, content in files.items():
            info = tarfile.TarInfo(rel)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return path


def _flat(text):
    return " ".join(text.split())


class FakeRepo:
    def has_synced_index(self):
        return True

    def get_package(self, name):
        return None


class FakeInstaller:
    def __init__(self):
        self.calls = []

    def install(self, name, **kw):
        self.calls.append({"name": name, **kw})
        return True, "installed"


class FakeRemover:
    calls = []

    def __init__(self, *a, **kw):
        pass

    def remove(self, name, **kw):
        FakeRemover.calls.append(name)
        return True, "removed"


class _Base(unittest.TestCase):
    """widget 1.0-3 installed, WITH its files and their recorded hashes —
    which is what a real install leaves behind and what the comparison
    reads."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        pkg_id = self.db.add_installed(NAME, "1.0", release=3, tier="core")
        for rel, content in PAYLOAD.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.db.add_files(pkg_id, list(PAYLOAD))
        FakeRemover.calls = []

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _args(self, archive, **over):
        base = dict(
            packages=[NAME], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=False, upgrade_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
            archive=str(archive), archive_trust="loose",
        )
        base.update(over)
        return argparse.Namespace(**base)

    def _run(self, args):
        installer = FakeInstaller()
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(output.NORMAL)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        rc = None
        try:
            with redirect_stdout(buf), \
                 patch.object(cli, "RepoManager", lambda *a, **k: FakeRepo()), \
                 patch.object(cli, "PackageInstaller",
                              lambda *a, **k: installer), \
                 patch("pkm.remover.PackageRemover", FakeRemover), \
                 patch.object(cli, "_save_rollback_archive",
                              lambda *a, **k: None), \
                 patch.object(cli,
                              "refresh_available_updates_after_transaction",
                              lambda db, **k: None), \
                 patch.object(cli, "_print_transaction_next_steps",
                              lambda *a, **k: None), \
                 patch.object(cli, "helper_is_present", lambda n: False), \
                 patch.object(cli, "helper_payload_present", lambda n: False), \
                 patch("pkm.pretxn.run_pre_transaction_hook",
                       lambda *a, **k: None):
                try:
                    rc = cli.cmd_upgrade(self.db, args)
                except SystemExit as e:
                    rc = e.code
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        return rc, buf.getvalue(), installer


class TheComparisonItself(_Base):

    def test_an_identical_archive_matches(self):
        same = _make_archive(self.tmp / "same.igos.tar.gz", NAME, "1.0", 3,
                             PAYLOAD)
        verdict, detail = cli._archive_matches_installed_build(
            self.db, NAME, same)
        self.assertEqual(verdict, "match", detail)
        self.assertIn("2 file(s)", detail)

    def test_one_changed_byte_is_a_different_build(self):
        changed = dict(PAYLOAD)
        changed["usr/share/widget/data"] = b"a\nb\nd\n"
        other = _make_archive(self.tmp / "other.igos.tar.gz", NAME, "1.0", 3,
                              changed)
        verdict, detail = cli._archive_matches_installed_build(
            self.db, NAME, other)
        self.assertEqual(verdict, "differs")
        self.assertIn("usr/share/widget/data", detail)
        self.assertIn(hashlib.sha256(b"a\nb\nd\n").hexdigest(), detail)

    def test_an_extra_file_in_the_archive_is_a_different_build(self):
        extra = dict(PAYLOAD)
        extra["usr/bin/extra"] = b"new\n"
        other = _make_archive(self.tmp / "extra.igos.tar.gz", NAME, "1.0", 3,
                              extra)
        verdict, detail = cli._archive_matches_installed_build(
            self.db, NAME, other)
        self.assertEqual(verdict, "differs")
        self.assertIn("usr/bin/extra", detail)

    def test_a_file_the_archive_is_missing_is_a_different_build(self):
        fewer = {"usr/bin/widget": PAYLOAD["usr/bin/widget"]}
        other = _make_archive(self.tmp / "fewer.igos.tar.gz", NAME, "1.0", 3,
                              fewer)
        verdict, detail = cli._archive_matches_installed_build(
            self.db, NAME, other)
        self.assertEqual(verdict, "differs")
        self.assertIn("usr/share/widget/data", detail)

    def test_a_build_with_no_recorded_content_is_not_a_match(self):
        """An absence of evidence is not evidence of identity."""
        with PackageDB(self.tmp / "bare.db", root=str(self.root)) as bare:
            bare.add_installed(NAME, "1.0", release=3, tier="core")
            same = _make_archive(self.tmp / "s2.igos.tar.gz", NAME, "1.0", 3,
                                 PAYLOAD)
            verdict, detail = cli._archive_matches_installed_build(
                bare, NAME, same)
        self.assertEqual(verdict, "unrecorded")
        self.assertIn("none carries a recorded hash", detail)

    def test_an_unreadable_archive_is_not_a_match(self):
        broken = self.tmp / "broken.igos.tar.gz"
        broken.write_bytes(b"not a tar file at all")
        verdict, _detail = cli._archive_matches_installed_build(
            self.db, NAME, broken)
        self.assertNotEqual(verdict, "match")


class TheDecisionAtTheSameRelease(_Base):

    def test_the_same_build_is_still_nothing_to_do(self):
        same = _make_archive(self.tmp / f"{NAME}-1.0-3.igos.tar.gz", NAME,
                             "1.0", 3, PAYLOAD)
        rc, out, installer = self._run(self._args(same))
        self.assertEqual(rc, 0, out)
        self.assertIn("exactly the archive's build", _flat(out))
        self.assertEqual(installer.calls, [])
        self.assertEqual(FakeRemover.calls, [])

    def test_a_different_archive_at_the_same_release_is_deployed(self):
        """The red case: same version, same release, different content."""
        changed = dict(PAYLOAD)
        changed["usr/bin/widget"] = b"#!/bin/sh\necho three\necho three\n"
        other = _make_archive(self.tmp / f"{NAME}-1.0-3.igos.tar.gz", NAME,
                              "1.0", 3, changed)
        rc, out, installer = self._run(self._args(other))
        self.assertEqual(rc, 0, out)
        flat = _flat(out)
        self.assertNotIn("exactly the archive's build", flat)
        self.assertIn("is NOT the installed build", flat)
        self.assertIn("usr/bin/widget", flat)
        self.assertEqual(len(installer.calls), 1, out)
        self.assertEqual(installer.calls[0]["archive_path"], str(other))

    def test_an_unrecorded_build_is_deployed_and_says_why(self):
        with PackageDB(self.tmp / "bare.db", root=str(self.root)) as bare:
            bare.add_installed(NAME, "1.0", release=3, tier="core")
            same = _make_archive(self.tmp / f"{NAME}-1.0-3.igos.tar.gz", NAME,
                                 "1.0", 3, PAYLOAD)
            installer = FakeInstaller()
            buf = io.StringIO()
            prior = output.process_level()
            output.set_process_level(output.NORMAL)
            output._process_reporter.stream = buf
            output._process_reporter.err_stream = buf
            try:
                with redirect_stdout(buf), \
                     patch.object(cli, "RepoManager",
                                  lambda *a, **k: FakeRepo()), \
                     patch.object(cli, "PackageInstaller",
                                  lambda *a, **k: installer), \
                     patch("pkm.remover.PackageRemover", FakeRemover), \
                     patch.object(cli, "_save_rollback_archive",
                                  lambda *a, **k: None), \
                     patch.object(
                         cli, "refresh_available_updates_after_transaction",
                         lambda db, **k: None), \
                     patch.object(cli, "_print_transaction_next_steps",
                                  lambda *a, **k: None), \
                     patch.object(cli, "helper_is_present", lambda n: False), \
                     patch.object(cli, "helper_payload_present",
                                  lambda n: False), \
                     patch("pkm.pretxn.run_pre_transaction_hook",
                           lambda *a, **k: None):
                    try:
                        cli.cmd_upgrade(bare, self._args(same))
                    except SystemExit:
                        pass
            finally:
                output.set_process_level(prior)
                output._process_reporter.stream = None
                output._process_reporter.err_stream = None
        flat = _flat(buf.getvalue())
        self.assertIn("cannot be established", flat)
        self.assertNotIn("exactly the archive's build", flat)
        self.assertEqual(len(installer.calls), 1, buf.getvalue())

    def test_a_newer_archive_is_untouched_by_this(self):
        newer = _make_archive(self.tmp / f"{NAME}-1.0-4.igos.tar.gz", NAME,
                              "1.0", 4, PAYLOAD)
        rc, out, installer = self._run(self._args(newer))
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(installer.calls), 1)
        self.assertNotIn("is NOT the installed build", _flat(out))


if __name__ == "__main__":
    unittest.main()

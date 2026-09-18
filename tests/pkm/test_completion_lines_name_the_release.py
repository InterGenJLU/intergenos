#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The line a person reads at the end of a transaction names the release.

WHY THIS FILE EXISTS. The version alone cannot tell two builds apart. Measured
on an installed machine on 2026-09-18: upgrading the assistant from release 256
to release 269 printed "Upgraded intergen to 0.1.0", and removing it printed
"Removed intergen 0.1.0" — the same characters before and after, on the one
line that says what just happened. The package database, `pkm --version` and
the transaction plan all carried the release at the time; only the completion
lines dropped it. A person confirming what landed was told the one thing that
does not distinguish the two builds.

One test per verb, plus autoremove, which named neither version nor release.
The release is read back off the installed row — the authority for what is now
on the machine — through the same helpers the transaction lines use, so the two
renderings cannot drift apart.
"""
import argparse
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
from pkm.installer import PackageInstaller
from pkm.remover import PackageRemover

NAME = "release-line-demo"
VERSION = "1.0.0"
RELEASE = 7


def _archive(tmp: Path, name=NAME, version=VERSION, pkgrel=RELEASE) -> str:
    """A minimal real archive carrying a known pkgrel."""
    staging = tmp / f"build-{name}-{pkgrel}"
    (staging / "usr").mkdir(parents=True, exist_ok=True)
    (staging / "usr" / f"{name}.txt").write_text("payload\n")
    lines = [f"pkgname={name}", f"pkgver={version}", f"pkgrel={pkgrel}",
             "pkgdesc=test pkg", "license=GPL", "tier=core",
             "builddate=2026-09-18T00:00:00Z", "size=8", "filecount=1"]
    (staging / ".PKGINFO").write_text("\n".join(lines) + "\n")
    path = tmp / f"{name}-{version}-{pkgrel}.igos.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        tf.add(staging / ".PKGINFO", arcname=".PKGINFO")
        tf.add(staging / "usr" / f"{name}.txt", arcname=f"usr/{name}.txt")
    return str(path)


class _Reporter:
    """Records every line the remover prints, whatever channel it uses."""

    def __init__(self):
        self.lines = []

    def _record(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    info = warn = done = error = step = _record

    def __getattr__(self, _name):
        return self._record


class TheInstallMessageNamesTheRelease(unittest.TestCase):

    def test_a_completed_install_names_version_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            root = td / "root"
            root.mkdir()
            db = PackageDB(td / "pkm.db", root=str(root))
            try:
                inst = PackageInstaller(db, root=str(root))
                ok, msg = inst.install(NAME, archive_path=_archive(td),
                                       install_reason="manual")
                self.assertTrue(ok, msg)
                self.assertIn(f"{VERSION}-{RELEASE}", msg,
                              f"the install message dropped the release: {msg}")
            finally:
                db.close()

    def test_the_release_comes_from_the_row_not_from_the_archive(self):
        """The row the install just wrote is the authority for what is on the
        machine; the archive metadata is what was asked for. A row that cannot
        be read falls back to the bare version rather than inventing one."""
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            root = td / "root"
            root.mkdir()
            db = PackageDB(td / "pkm.db", root=str(root))
            try:
                inst = PackageInstaller(db, root=str(root))
                with patch.object(db, "get_installed", lambda *_a, **_k: None):
                    self.assertEqual(inst._installed_vr(NAME, VERSION), VERSION)
                db.add_installed(NAME, VERSION, release=RELEASE, tier="core")
                self.assertEqual(inst._installed_vr(NAME, VERSION),
                                 f"{VERSION}-{RELEASE}")
            finally:
                db.close()


class TheRemoveLineNamesTheRelease(unittest.TestCase):

    def test_the_printed_completion_line_names_version_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            root = td / "root"
            root.mkdir()
            db = PackageDB(td / "pkm.db", root=str(root))
            try:
                inst = PackageInstaller(db, root=str(root))
                ok, msg = inst.install(NAME, archive_path=_archive(td),
                                       install_reason="manual")
                self.assertTrue(ok, msg)
                reporter = _Reporter()
                rem = PackageRemover(db, root=str(root))
                ok, msg = rem.remove(NAME, reporter=reporter)
                self.assertTrue(ok, msg)
                printed = [ln for ln in reporter.lines if "Removed" in ln]
                self.assertTrue(printed, f"no Removed line: {reporter.lines}")
                self.assertIn(f"{VERSION}-{RELEASE}", " ".join(printed),
                              f"the printed remove line dropped the release: "
                              f"{printed}")
            finally:
                db.close()


class _FakeRepo:
    def __init__(self, remote):
        self.remote = remote

    def get_package(self, name):
        return self.remote.get(name)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class TheUpgradeLineNamesTheRelease(unittest.TestCase):
    """The verb the defect was measured on."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_the_printed_completion_line_names_the_new_release(self):
        db = self.db
        db.add_installed(NAME, VERSION, release=5, tier="core")
        remote = {NAME: {"name": NAME, "version": VERSION, "release": 9,
                         "sha256": "0" * 64, "size": 10, "depends": [],
                         "local_archive": True}}

        class _Installer:
            def install(self_inner, name, **kw):
                # What a real install does, and the only part this line reads:
                # the row is rewritten at the new release before the completion
                # line is printed.
                db.add_installed(name, VERSION, release=9, tier="core",
                                 replace_existing=True)
                return True, "installed"

            def __getattr__(self_inner, _n):
                return lambda *a, **k: None

        class _Remover:
            def __init__(self_inner, *a, **k):
                pass

            def remove(self_inner, name, **kw):
                return True, "removed"

        args = argparse.Namespace(
            packages=[NAME], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=False, assume_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
        )
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(output.NORMAL)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        try:
            with redirect_stdout(buf), \
                 patch.object(cli, "RepoManager", lambda *a, **k: _FakeRepo(remote)), \
                 patch.object(cli, "PackageInstaller", lambda *a, **k: _Installer()), \
                 patch("pkm.remover.PackageRemover", _Remover), \
                 patch.object(cli, "_confirm_upgrade", lambda _a: True), \
                 patch.object(cli, "_save_rollback_archive", lambda *a, **k: None), \
                 patch.object(cli, "refresh_available_updates_after_transaction",
                              lambda db_, **k: None), \
                 patch.object(cli, "_print_transaction_next_steps", lambda *a, **k: None), \
                 patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                cli.cmd_upgrade(db, args)
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        out = buf.getvalue()
        # ASSERT ON THE COMPLETION LINE, NOT ON THE WHOLE TRANSCRIPT. The plan
        # summary printed earlier in the same run is already release-bearing
        # ("... 1.0.0-5 -> 1.0.0-9"), so a search of the whole output passes
        # whether or not the completion line carries the release — measured
        # while writing this test, which passed against the unfixed code for
        # exactly that reason.
        upgraded = [ln for ln in out.splitlines() if "Upgraded" in ln]
        self.assertTrue(upgraded, f"no completion line at all:\n{out}")
        self.assertIn(f"{VERSION}-9", " ".join(upgraded),
                      f"the upgrade completion line dropped the release: "
                      f"{upgraded}")


class TheAutoremoveLineNamesTheRelease(unittest.TestCase):
    """It named neither the version nor the release — a package disappeared
    from the machine over a line that said only its name."""

    def test_the_orphan_row_carries_the_release_so_the_line_can_name_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            db = PackageDB(td / "pkm.db", root=str(td / "root"))
            try:
                db.add_installed(NAME, VERSION, release=RELEASE, tier="core",
                                 install_reason="dependency")
                rows = db.find_orphan_packages()
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(rows[0].get("release"), RELEASE,
                                 "an orphan row without its release would be "
                                 "rendered at the schema default of 1, which "
                                 "prints a release the package does not have")
                from pkm import txn
                self.assertIn(f"{VERSION}-{RELEASE}",
                              txn.describe_subject(NAME, rows[0]))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()

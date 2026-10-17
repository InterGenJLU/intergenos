#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An install that landed degraded is named as such, never as one that did not land.

install() returns False both when a package did not land and when it landed
and a critical post-install hook then failed — a hook that refused to sign
among them, which marks the row DEGRADED. The callers that replace a package
read every False as "did not land" (found by a second read, 2026-10-17):

  - `pkm reinstall` printed "<name> is removed but not reinstalled. Run
    `pkm install <name>` to recover" while the package was installed, and that
    command refuses an installed package;
  - `pkm upgrade` tried to install the rollback archive over the release that
    had landed, which refused as well, and printed CRITICAL.

This drives the REAL commands — the parser's own arguments, the real database,
installer and remover over a fixture root — with the package's own
post-install hook really run (directly instead of under chroot, which a test
cannot use), its exit status read from a file so one archive can succeed and
then refuse. Nothing is fetched: the archives are local, and the repository is
a stand-in that describes them.
"""

import hashlib
import io
import os
import shutil
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.installer as installer_mod
from pkm import cli
from pkm.database import PackageDB
from pkm.hooks import HookResult
from pkm.installer import PackageInstaller

PKG = "kpkg"


def _archive(path, version, rc_file):
    staging = path.parent / f"stage-{version}"
    (staging / "usr" / "bin").mkdir(parents=True)
    (staging / "usr" / "bin" / PKG).write_text(f"#!/bin/sh\necho {version}\n")
    hook_rel = f"var/lib/pkm/hooks/{PKG}/post-install"
    (staging / hook_rel).parent.mkdir(parents=True)
    (staging / hook_rel).write_text(f'#!/bin/sh\nexit "$(cat {rc_file})"\n')
    (staging / hook_rel).chmod(0o755)
    (staging / ".PKGINFO").write_text(
        f"pkgname={PKG}\npkgver={version}\npkgrel=1\npkgdesc=x\nlicense=GPL\n"
        f"tier=core\nbuilddate=2026-10-17T00:00:00Z\nsize=8\nfilecount=2\n")
    with tarfile.open(path, "w:gz") as tf:
        tf.add(staging / ".PKGINFO", arcname=".PKGINFO")
        tf.add(staging / "usr" / "bin" / PKG, arcname=f"usr/bin/{PKG}")
        tf.add(staging / hook_rel, arcname=hook_rel)
    shutil.rmtree(staging)
    return path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _StandInRepo:
    """Describes the local archives; fetches nothing."""

    def __init__(self, entries):
        self.entries = entries

    def get_package(self, name):
        return self.entries.get(name)

    def download_package(self, name, reporter=None):
        raise AssertionError("nothing may be fetched in this test")

    def resolve_dependencies(self, name, db):
        raise AssertionError(
            f"{name} landed, and the command went to the repository to resolve "
            f"and install it again")


def _run_directly(root, hook):
    return [str(hook)]


class _Fixture(unittest.TestCase):
    install_first = True

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.rc = self.tmp / "hook-rc"
        self.rc.write_text("0\n")
        self.v1 = _archive(self.tmp / f"{PKG}-1.0.igos.tar.gz", "1.0", self.rc)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        self.addCleanup(self.db.close)
        cli.set_install_root(self.root)
        self.addCleanup(cli.set_install_root, None)
        handlers = self.tmp / "pretxn.d"
        handlers.mkdir()
        for p in (
            patch.dict(os.environ, {"PKM_PRETXN_HANDLER_DIR": str(handlers)}),
            patch.object(installer_mod, "_post_install_hook_cmd", _run_directly),
            patch.object(installer_mod, "_TRACE_AVAILABLE", False),
            patch("pkm.installer.run_canonical_hooks",
                  return_value=HookResult([], [], [])),
            patch("pkm.installer.run_archive_lifecycle_hook",
                  return_value=HookResult([], [], [])),
        ):
            p.start()
            self.addCleanup(p.stop)
        if not self.install_first:
            return
        ok, msg = PackageInstaller(self.db, root=str(self.root)).install(
            PKG, archive_path=str(self.v1))
        self.assertTrue(ok, msg)
        self.assertFalse(self.db.get_installed(PKG)["degraded"])

    def _cli(self, argv, repo):
        args = cli.build_parser().parse_args(argv)
        command = {"reinstall": cli.cmd_reinstall,
                   "upgrade": cli.cmd_upgrade,
                   "install": cli.cmd_install}[args.command]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with patch.object(cli, "repo_manager", return_value=repo), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                ret = command(self.db, args)
                code = ret if isinstance(ret, int) else 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        said = " ".join((out.getvalue() + " " + err.getvalue()).split())
        return code, said


class ReinstallAfterARefusedSigning(_Fixture):
    def test_reinstall_names_the_unfinished_install_and_a_command_that_works(self):
        self.rc.write_text("77\n")
        repo = _StandInRepo({PKG: {"name": PKG, "version": "1.0", "release": 1,
                                   "sha256": _sha(self.v1)}})
        with patch.object(PackageInstaller, "_find_archive",
                          return_value=self.v1), \
             patch.object(cli, "_trusted_cached_archive",
                          return_value=(self.v1, _sha(self.v1))):
            code, said = self._cli(["reinstall", PKG], repo)
        row = self.db.get_installed(PKG)
        self.assertIsNotNone(row, "the package must still be installed")
        self.assertIn("refused to sign", row["degraded"] or "")
        self.assertNotEqual(code, 0)
        self.assertNotIn("removed but not reinstalled", said)
        self.assertNotIn(f"pkm install {PKG}", said)
        self.assertIn(f"{PKG} is reinstalled, but its install did not finish", said)
        self.assertIn(f"sudo pkm reinstall {PKG}", said)
        self.assertIn("not the MOK enrollment password", said)


class UpgradeAfterARefusedSigning(_Fixture):
    def test_upgrade_names_the_unfinished_install_and_does_not_roll_back(self):
        cache = Path(cli.repo_pkg_cache())
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.v1, cache / self.v1.name)   # the rollback copy's source
        v2 = _archive(self.tmp / f"{PKG}-1.1.igos.tar.gz", "1.1", self.rc)
        self.rc.write_text("77\n")
        repo = _StandInRepo({})
        code, said = self._cli(
            ["upgrade", PKG, "--archive", str(v2), "--archive-trust", "loose",
             "--yes"], repo)
        row = self.db.get_installed(PKG)
        self.assertIsNotNone(row, "the new release landed and must stay installed")
        self.assertEqual(row["version"], "1.1")
        self.assertIn("refused to sign", row["degraded"] or "")
        self.assertNotEqual(code, 0)
        self.assertNotIn("CRITICAL: rollback", said)
        self.assertNotIn("restoring", said)
        self.assertIn("but its install did not finish", said)
        self.assertIn("nothing was rolled back", said)
        self.assertIn(f"sudo pkm reinstall {PKG}", said)
        self.assertIn(f"1 of 1 package(s) upgraded but did not finish: {PKG}", said)
        self.assertNotIn("did not upgrade", said)


class InstallThatLandedDegraded(_Fixture):
    """The same rule on `pkm install`'s local-archive path, which read a
    not-ok result by its text: "not found" anywhere in it sent the command to
    the repository to resolve and install the package again. A critical hook's
    own output is part of that text — here the package's sealed post_install
    fails with "grub-mkconfig: not found", a shape a missing tool produces."""
    install_first = False

    def test_install_names_the_unfinished_install_instead_of_resolving_again(self):
        critical = HookResult(["post_install"], [], [
            "  hook[archive/post_install] CRITICAL: exit 127; "
            "sh: grub-mkconfig: not found"])
        repo = _StandInRepo({})
        with patch("pkm.installer.run_archive_lifecycle_hook",
                   return_value=critical):
            code, said = self._cli(
                ["install", PKG, "--archive", str(self.v1), "--archive-trust",
                 "loose", "--yes"], repo)
        row = self.db.get_installed(PKG)
        self.assertIsNotNone(row)
        self.assertTrue(row["degraded"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("Resolving", said)
        self.assertIn(f"{PKG} is installed, but its install did not finish", said)
        self.assertIn(f"Retry it with: sudo pkm reinstall {PKG}", said)


class TheRuleItself(unittest.TestCase):
    def test_a_landed_degraded_package_is_told_apart_from_one_that_did_not_land(self):
        with tempfile.TemporaryDirectory() as td:
            db = PackageDB(Path(td) / "pkm.db", root=str(Path(td) / "root"))
            try:
                db.add_installed(PKG, "1.0", tier="core")
                self.assertIsNone(cli._unfinished_install(db, PKG, False))
                db.mark_degraded(PKG, f"{PKG} post-install (refused to sign)")
                self.assertEqual(cli._unfinished_install(db, PKG, False),
                                 f"{PKG} post-install (refused to sign)")
                self.assertIsNone(cli._unfinished_install(db, PKG, True))
                self.assertIsNone(cli._unfinished_install(db, "absent", False))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()

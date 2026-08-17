#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A25 regression: degraded marker for critical-hook failures + daemon-reload.

When a critical post-install hook (e.g. UKI rebuild/sign, depmod) failed, the
package was fully deployed + DB-registered yet install returned False with only
"rollback recommended" — so it masqueraded as a generic failure while `pkm
list`/`verify` showed it as a normal install. Fixed: a durable `degraded` marker
on the installed row, set on critical-hook failure and surfaced by list + verify
(cleared by a successful reinstall). Separately, a silent systemd daemon-reload
failure (intentionally non-critical) now emits a specific remediation line.
"""

import argparse
import io
import os
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm.database import PackageDB
from pkm.hooks import HookResult
from pkm.installer import PackageInstaller


def _build_archive(tmp, name, version):
    staging = Path(tmp) / f"b-{name}"
    (staging / "usr" / "bin").mkdir(parents=True)
    (staging / "usr" / "bin" / name).write_text("#!/bin/sh\n")
    (staging / ".PKGINFO").write_text(
        f"pkgname={name}\npkgver={version}\npkgrel=1\npkgdesc=x\nlicense=GPL\n"
        f"tier=core\nbuilddate=2026-06-16T00:00:00Z\nsize=8\nfilecount=1\n")
    archive = Path(tmp) / f"{name}-{version}.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(staging / ".PKGINFO", arcname=".PKGINFO")
        tf.add(staging / "usr" / "bin" / name, arcname=f"usr/bin/{name}")
    return str(archive)


class DegradedDbTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = PackageDB(Path(self._td.name) / "pkm.db",
                            root=str(Path(self._td.name) / "root"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_mark_degraded_surfaces_in_list_and_get(self):
        self.db.add_installed("kernel", "6.10", tier="core")
        self.assertIsNone(self.db.get_installed("kernel")["degraded"])
        self.db.mark_degraded("kernel", "uki-sign, depmod")
        self.assertEqual(self.db.get_installed("kernel")["degraded"],
                         "uki-sign, depmod")
        row = [p for p in self.db.list_installed() if p["name"] == "kernel"][0]
        self.assertEqual(row["degraded"], "uki-sign, depmod")

    def test_reinstall_clears_degraded(self):
        self.db.add_installed("kernel", "6.10", tier="core")
        self.db.mark_degraded("kernel", "uki-sign")
        self.assertTrue(self.db.get_installed("kernel")["degraded"])
        # A fresh add_installed (INSERT OR REPLACE on UNIQUE name) resets it.
        # The replace is declared because it is the point of the test.
        self.db.add_installed("kernel", "6.11", tier="core",
                              replace_existing=True)
        self.assertIsNone(self.db.get_installed("kernel")["degraded"])


class DegradedInstallerTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def test_critical_hook_marks_degraded_and_returns_false(self):
        archive = _build_archive(self.tmp, "kpkg", "1.0")
        db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

        def fake_canonical(root, file_list, name, version, op, hooks=None):
            # The POST call (hooks=None) reports a critical failure.
            if hooks is None:
                return HookResult(["uki-sign"], [], [])
            return HookResult([], [], [])

        try:
            inst = PackageInstaller(db, root=str(self.root))
            with patch("pkm.installer.run_canonical_hooks", fake_canonical), \
                 patch("pkm.installer.run_archive_lifecycle_hook",
                       return_value=HookResult([], [], [])):
                ok, msg = inst.install("kpkg", archive_path=archive)
            self.assertFalse(ok)
            self.assertIn("DEGRADED", msg)
            self.assertIn("uki-sign", msg)
            # Durable marker recorded on the (committed) row.
            self.assertEqual(db.get_installed("kpkg")["degraded"], "uki-sign")
        finally:
            db.close()

    def test_daemon_reload_failure_emits_remediation(self):
        archive = _build_archive(self.tmp, "svcpkg", "1.0")
        db = PackageDB(self.tmp / "pkm2.db", root=str(self.root))

        def fake_canonical(root, file_list, name, version, op, hooks=None):
            if hooks is None:
                return HookResult([], ["systemd-daemon-reload"], [])
            return HookResult([], [], [])

        try:
            inst = PackageInstaller(db, root=str(self.root))
            with patch("pkm.installer.run_canonical_hooks", fake_canonical), \
                 patch("pkm.installer.run_archive_lifecycle_hook",
                       return_value=HookResult([], [], [])):
                ok, msg = inst.install("svcpkg", archive_path=archive)
            self.assertTrue(ok, msg)
            self.assertIn("systemctl daemon-reload", msg)
            # Not degraded (daemon-reload is non-critical).
            self.assertIsNone(db.get_installed("svcpkg")["degraded"])
        finally:
            db.close()


class DegradedCliTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = PackageDB(Path(self._td.name) / "pkm.db",
                            root=str(Path(self._td.name) / "root"))
        self.db.add_installed("kernel", "6.10", tier="core")
        self.db.mark_degraded("kernel", "uki-sign")

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_cmd_list_shows_degraded_tag(self):
        args = argparse.Namespace(what="installed", tier=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_list(self.db, args)
        self.assertIn("[DEGRADED", buf.getvalue())
        self.assertIn("uki-sign", buf.getvalue())

    def test_cmd_verify_reports_degraded(self):
        args = argparse.Namespace(verify_all=False, package="kernel",
                                  verify_mode="strict")
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(buf):
            cli.cmd_verify(self.db, args)
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("DEGRADED", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

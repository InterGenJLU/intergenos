#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A24 (setuid half): a setuid/setgid restore failure aborts, not WARNs.

The hardened-tar 'data' filter strips setuid/setgid/sticky bits on extract;
install() then re-applies them from the archive members. The whole restore loop
was wrapped in one try/except that, on any chmod failure, only printed a WARNING
and continued — so a privileged binary (sudo/su/mount/...) could land non-setuid
SILENTLY (a security-relevant masked failure). Fixed: a setuid/setgid restore
failure on a member that carries those bits fails the install closed; a
sticky-only failure stays a warning.

(The other half of PKM-A24 — FS-deploy-precedes-DB-commit — is an
install-atomicity design decision surfaced to the operator, tracked separately.)
"""

import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


def _build_setuid_archive(tmp, name, version):
    staging = Path(tmp) / f"build-{name}"
    bindir = staging / "usr" / "bin"
    bindir.mkdir(parents=True)
    priv = bindir / "priv"
    priv.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(priv, 0o4755)  # setuid root binary
    (staging / ".PKGINFO").write_text(
        f"pkgname={name}\npkgver={version}\npkgrel=1\npkgdesc=priv tool\n"
        f"license=GPL\ntier=core\nbuilddate=2026-06-16T00:00:00Z\n"
        f"size=8\nfilecount=1\n")
    archive = Path(tmp) / f"{name}-{version}.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(staging / ".PKGINFO", arcname=".PKGINFO")
        tf.add(priv, arcname="usr/bin/priv")
    return str(archive)


class SetuidFailClosedTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def test_setuid_restore_failure_aborts_install(self):
        archive = _build_setuid_archive(self.tmp, "priv-pkg", "1.0")
        db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        try:
            inst = PackageInstaller(db, root=str(self.root))
            real_chmod = Path.chmod

            def fake_chmod(self_, mode, *a, **k):
                # Fail ONLY the setuid-bit restore; let every other chmod pass.
                if mode & stat.S_ISUID:
                    raise OSError(1, "operation not permitted")
                return real_chmod(self_, mode, *a, **k)

            with patch.object(Path, "chmod", fake_chmod):
                ok, msg = inst.install("priv-pkg", archive_path=archive)
            self.assertFalse(ok, "setuid restore failure must abort, not WARN")
            self.assertIn("setuid", msg.lower())
            self.assertIn("priv", msg)
            # Not registered (aborted before the DB transaction commit).
            self.assertIsNone(db.get_installed("priv-pkg"))
        finally:
            db.close()

    def test_setuid_bit_restored_on_success(self):
        archive = _build_setuid_archive(self.tmp, "priv2", "1.0")
        db = PackageDB(self.tmp / "pkm2.db", root=str(self.root))
        try:
            inst = PackageInstaller(db, root=str(self.root))
            ok, msg = inst.install("priv2", archive_path=archive)
            self.assertTrue(ok, f"install failed: {msg}")
            deployed = self.root / "usr" / "bin" / "priv"
            self.assertTrue(deployed.exists())
            self.assertTrue(
                os.stat(deployed).st_mode & stat.S_ISUID,
                "setuid bit was not restored on the deployed binary")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

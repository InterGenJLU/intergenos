#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The bytecode purge runs BEFORE the archive lands, so the archive's own
compiled files survive.

MEASURED on two fresh R001.2 installations on 2026-09-02 (the borrowed
ThinkPad and the custom-built AMD desktop PC): `pkm verify` reported
thousands of files missing, every one of them a `__pycache__/*.pyc` that a
package's archive ships, records and checksums. The purge that protects an
UPGRADE from stale bytecode (tests/pkm/test_stale_bytecode_purge.py) ran after
the extract and deleted the compiled files the extract had just deployed.

The purge belongs before the extract: it then removes only what a previous
version left behind, and the archive's compiled files land afterwards and
stay. The upgrade protection is unchanged — the stale cache is gone before the
new source is read — and the reproduction case in the sibling file still
passes.

The last case here is the one that matters: a REAL install of an archive that
ships a module and its compiled file, into a scratch root, through the shipped
installer; afterwards the compiled file must be on disk and `verify` must not
call it missing.
"""

import io
import py_compile
import tarfile
import tempfile
import unittest
from pathlib import Path


def _archive_with_module_and_bytecode(tmp):
    """A well-formed package archive: one module plus its __pycache__ entry,
    compiled the way the build compiles it."""
    src_root = Path(tmp) / "src"
    mod_dir = src_root / "usr" / "lib" / "demo"
    mod_dir.mkdir(parents=True)
    mod = mod_dir / "greet.py"
    mod.write_text("VALUE = 'NEW'\n")
    pyc = Path(py_compile.compile(str(mod), doraise=True))
    lines = [
        "pkgname=pycdemo", "pkgver=1.0", "pkgrel=1",
        "pkgdesc=bytecode-order test package", "license=GPL", "tier=core",
        "builddate=2026-09-02T00:00:00Z", "size=64", "filecount=2",
    ]
    archive = Path(tmp) / "pycdemo-1.0.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = ("\n".join(lines) + "\n").encode()
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
        tf.add(mod, arcname="./usr/lib/demo/greet.py")
        tf.add(pyc, arcname="./usr/lib/demo/__pycache__/" + pyc.name)
    return archive, "usr/lib/demo/__pycache__/" + pyc.name


class ThePurgeSitsBeforeTheExtract(unittest.TestCase):

    def test_the_purge_is_called_before_the_deploy_extract(self):
        from pkm import installer as installer_mod
        src = Path(installer_mod.__file__).read_text(encoding="utf-8")
        deploy_marker = "ok, err = _safe_extract_tar("
        self.assertIn(deploy_marker, src)
        # The LAST extract in the installer is the deploy; the first is the
        # staging extract the file list is built from.
        before, after = src.rsplit(deploy_marker, 1)
        # Within the install method: after the file list is built from the
        # staging tree (the purge needs it) and before the extract.
        window = before.rsplit("file_list = []", 1)[1]
        self.assertIn("_purge_stale_bytecode(self.root, file_list)", window,
                      "the purge does not run before the archive is written "
                      "into place")
        after_window = after.split("version = self._version_from_archive", 1)[0]
        self.assertNotIn("_purge_stale_bytecode(", after_window,
                         "the purge still runs after the extract, where it "
                         "deletes the compiled files the archive just shipped")


class TheArchivesOwnBytecodeSurvivesAnInstall(unittest.TestCase):

    def test_a_real_install_keeps_the_shipped_pyc_and_verify_sees_it(self):
        import importlib
        rootpaths = importlib.import_module("pkm.rootpaths")
        from pkm.database import PackageDB
        from pkm.installer import PackageInstaller
        from pkm.verifier import PackageVerifier

        with tempfile.TemporaryDirectory() as tmp:
            archive, pyc_rel = _archive_with_module_and_bytecode(tmp)
            target = Path(tmp) / "target"
            (target / "etc").mkdir(parents=True)
            (target / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
            (target / "etc" / "group").write_text("root:x:0:\n")

            db = PackageDB(str(rootpaths.db_path(target)), root=str(target))
            try:
                installer = PackageInstaller(db, root=str(target))
                ok, msg = installer.install("pycdemo", archive_path=str(archive))
                self.assertTrue(ok, f"install failed: {msg}")
                self.assertTrue(
                    (target / pyc_rel).is_file(),
                    "the compiled file the archive shipped was deleted by the "
                    "install — this is what made `pkm verify` report thousands "
                    "of files missing on every fresh installation")
                result = PackageVerifier(db).verify("pycdemo", mode="strict")
                self.assertEqual(result["missing"], [],
                                 f"verify reports missing files: {result['missing']}")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()

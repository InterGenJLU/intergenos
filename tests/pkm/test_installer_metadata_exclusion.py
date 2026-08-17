# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Deploy must NOT write archive-metadata (.PKGINFO, package.yml) to the target
root — H-008. This is also the FORGE guarantee: the OS installer's package path
(installer/backend/packages.py install_packages) funnels every package through
PackageInstaller.install() and never extracts archives itself, so the pkm deploy
exclusion proven here is exactly what Forge inherits — there is no separate Forge
extract path that could leak the metadata onto the installed root.

Demonstrated end-to-end (absence of the defect is SHOWN, not assumed): install an
archive that CONTAINS .PKGINFO + package.yml and assert both are absent from the
target root while the real payload lands.

Regression note (work-plan 1.20): the fixture MUST build the archive the way the
real archiver does — `tar -C <dest> -czf <archive> .` (pkg-functions.sh:1135),
which stores every member `./`-prefixed (`./.PKGINFO`). An earlier fixture used
bare `arcname=".PKGINFO"`, which matched the exclusion by accident and hid a live
leak: the deploy filter tested the raw `./`-prefixed member name against an
un-prefixed exclude set, so real archives deposited .PKGINFO/package.yml on the
root. Building in the real `./` form is the load-bearing part of this guard.
"""
from __future__ import annotations

import tarfile
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.installer import (
    PackageInstaller,
    _ARCHIVE_METADATA_FILES,
    _safe_extract_tar,
)


class MetadataExclusionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "t.db")
        self.installer = PackageInstaller(self.db, root=str(self.root))

    def tearDown(self):
        self.db.close()

    def _archive_with_metadata(self, name: str, version: str) -> Path:
        stg = self.tmp / f"stg-{name}"
        (stg / "usr" / "bin").mkdir(parents=True)
        (stg / "usr" / "bin" / name).write_text("#!/bin/sh\nexit 0\n")
        (stg / ".PKGINFO").write_text(
            f"pkgname = {name}\npkgver = {version}\n")
        (stg / "package.yml").write_text(f"name: {name}\nversion: \"{version}\"\n")
        arc = self.tmp / f"{name}-{version}.igos.tar.gz"
        # Build the archive in the archiver's real form: `tar -C stg .` stores
        # every member `./`-prefixed. tarfile's add(dir, arcname=".") mirrors
        # that recursively (`./`, `./.PKGINFO`, `./usr/bin/<name>`, ...). Do NOT
        # revert to per-member bare arcnames — that hid work-plan 1.20.
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(stg, arcname=".")
        return arc

    def _real_form_archive(self, name, members):
        """Archive `members` ({relpath: text}) in the real `./`-prefixed form."""
        stg = self.tmp / f"cfg-{name}"
        for rel, text in members.items():
            p = stg / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        arc = self.tmp / f"{name}.tar.gz"
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(stg, arcname=".")
        return arc

    def test_metadata_constant_names_the_two_files(self):
        self.assertIn(".PKGINFO", _ARCHIVE_METADATA_FILES)
        self.assertIn("package.yml", _ARCHIVE_METADATA_FILES)

    def test_archive_stores_members_dot_slash_prefixed(self):
        # The fixture is only a valid regression guard if it reproduces the real
        # archiver's `./`-prefixed member names. Assert that invariant directly.
        arc = self._archive_with_metadata("shape", "1.0")
        with tarfile.open(arc) as tf:
            names = tf.getnames()
        self.assertIn("./.PKGINFO", names)
        self.assertIn("./package.yml", names)
        self.assertTrue(any(n.startswith("./usr/bin/") for n in names))

    def test_deploy_exclude_honored_for_dot_slash_config_paths(self):
        # Mechanism-level guard for the Q4 config-protection deploy-exclude leg
        # (same root cause as the .PKGINFO leak): an un-prefixed exclude path
        # (`etc/foo.conf`, as prepare_config_protection derives it from the
        # file_list) must exclude the `./`-prefixed member the real archive
        # carries. Pre-fix this clobbered the user-edited config it should skip.
        arc = self._real_form_archive("cfg", {
            "etc/keep.conf": "payload\n",
            "etc/user_edited.conf": "PACKAGE DEFAULT (must not clobber)\n",
        })
        dest = self.tmp / "cfgroot"
        dest.mkdir()
        ok, err = _safe_extract_tar(
            str(arc), str(dest), exclude_paths={"etc/user_edited.conf"})
        self.assertTrue(ok, err)
        self.assertTrue((dest / "etc" / "keep.conf").exists(),
                        "non-excluded config must deploy")
        self.assertFalse((dest / "etc" / "user_edited.conf").exists(),
                         "excluded (user-edited) config must NOT be deployed")

    def test_install_deploys_payload_but_not_metadata(self):
        ok, msg = self.installer.install(
            "demo", archive_path=self._archive_with_metadata("demo", "1.0"))
        self.assertTrue(ok, f"install must succeed: {msg}")

        # The real payload IS on the installed root.
        self.assertTrue((self.root / "usr" / "bin" / "demo").exists(),
                        "the package's real file must be deployed")
        # The archive-metadata files are NOT (H-008 / the Forge guarantee).
        self.assertFalse((self.root / ".PKGINFO").exists(),
                         ".PKGINFO must never land on the installed root")
        self.assertFalse((self.root / "package.yml").exists(),
                         "package.yml must never land on the installed root")


if __name__ == "__main__":
    unittest.main()

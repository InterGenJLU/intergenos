#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/pkg-functions.sh pkg_manifest — the bash lane's manifest shape.

The bash builder builds the core, base and core-extra tiers, and it does not
write the pkm database itself: it writes a text manifest and then shells out to
`pkm import`. Every row for the largest part of the corpus is therefore
constructed from these bytes, and whatever the manifest fails to say, the
database cannot know.

Two things it failed to say, both load-bearing:

  - a directory carried no trailing slash. pkm derives files.is_dir purely from
    that marker, so every directory of every bash-tier package registered as a
    FILE. pkm/remover.py already documents the consequence at scale ("the
    ge9b-08 chroot DB: thousands of real directories carried is_dir=0") and
    chose disk truth over the flag to survive it; verify still reports an
    absent directory as a missing file.
  - a file carried no sha256. import then had no recorded reference and fell
    through to hashing whatever was on disk at import time, so the bash tier
    had no independent content record at all — the database agreed with the
    filesystem by construction, which is exactly the property a content check
    is supposed to test rather than assume.

The annotation is anchored at end of line rather than delimited, and this test
pins that with a path containing spaces: linux-firmware ships several, and a
whitespace-splitting producer or consumer truncates them to a path that does
not exist.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from pkm.database import _parse_manifest, _sha256

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_FUNCTIONS = REPO_ROOT / "scripts" / "pkg-functions.sh"

SHA_LINE = re.compile(r"^(?P<path>.+) sha256:(?P<hash>[0-9a-f]{64})$")


class BashManifestShapeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.staging = self.base / "staging"
        self.pkgdb = self.base / "pkgdb"
        self.staging.mkdir()
        self.pkgdb.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, name="demo", version="1.0", release="3"):
        # pkg-functions.sh assigns its path variables unconditionally at
        # source time, so the test roots are set AFTER the source.
        logs = self.base / "logs"
        logs.mkdir(exist_ok=True)
        script = (
            f'set -e; source "{PKG_FUNCTIONS}" >/dev/null 2>&1; '
            f'IGOS_PKG_STAGING="{self.staging}"; '
            f'IGOS_PKG_DB="{self.pkgdb}"; '
            f'IGOS_LOGS="{logs}"; '
            f'pkg_manifest "{name}" "{version}" "a demo package" "{release}"'
        )
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.pkgdb / f"{name}-{version}").read_text()

    def _stage(self, rel, content=b"payload\n"):
        p = self.staging / "demo-1.0" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def _file_list(self, manifest_text):
        lines = manifest_text.splitlines()
        return lines[lines.index("FILE LIST:") + 1:]

    # -- shape -----------------------------------------------------------

    def test_directories_carry_a_trailing_slash_and_files_do_not(self):
        self._stage("usr/bin/demo")
        entries = self._file_list(self._run())
        self.assertIn("usr/", entries)
        self.assertIn("usr/bin/", entries)
        self.assertTrue(
            any(e.startswith("usr/bin/demo ") for e in entries),
            f"the file entry is missing from {entries}")
        self.assertNotIn("usr/bin/demo/", entries)

    def test_every_regular_file_carries_its_staged_sha256(self):
        p = self._stage("usr/bin/demo", b"exact bytes\n")
        expected = _sha256(str(p))
        entries = self._file_list(self._run())
        matched = [SHA_LINE.match(e) for e in entries]
        got = {m.group("path"): m.group("hash") for m in matched if m}
        self.assertEqual(got.get("usr/bin/demo"), expected)

    def test_a_path_containing_spaces_survives_intact(self):
        """linux-firmware ships these; a delimiter-split producer truncates."""
        rel = "usr/lib/firmware/brcmfmac43455-sdio.Raspberry Pi Foundation.txt"
        p = self._stage(rel, b"blob\n")
        text = self._run()
        parsed = _parse_manifest(text)
        self.assertIn(rel, parsed["files"])
        self.assertEqual(parsed["file_hashes"].get(rel), _sha256(str(p)))

    def test_a_symlink_is_listed_bare(self):
        d = self.staging / "demo-1.0" / "usr" / "bin"
        d.mkdir(parents=True)
        (d / "real").write_bytes(b"x\n")
        os.symlink("real", d / "link")
        entries = self._file_list(self._run())
        self.assertIn("usr/bin/link", entries)

    def test_a_symlinked_directory_is_not_marked_as_a_directory(self):
        """find reports it as type l; marking it a dir misleads the remover."""
        root = self.staging / "demo-1.0"
        (root / "usr" / "share" / "real").mkdir(parents=True)
        os.symlink("real", root / "usr" / "share" / "alias")
        entries = self._file_list(self._run())
        self.assertIn("usr/share/real/", entries)
        self.assertIn("usr/share/alias", entries)
        self.assertNotIn("usr/share/alias/", entries)

    # -- what pkm makes of it -------------------------------------------

    def test_pkm_parses_the_result_and_recovers_dirs_hashes_and_release(self):
        p = self._stage("usr/bin/demo", b"content\n")
        parsed = _parse_manifest(self._run())
        self.assertEqual(parsed["name"], "demo")
        self.assertEqual(parsed["version"], "1.0")
        self.assertEqual(parsed["release"], 3)
        self.assertIn("usr/bin/", parsed["files"])
        self.assertEqual(parsed["file_hashes"]["usr/bin/demo"],
                         _sha256(str(p)))

    def test_registration_from_this_manifest_marks_directories_as_directories(self):
        import sqlite3
        from pkm.database import PackageDB
        self._stage("usr/bin/demo")
        text = self._run()
        mdir = self.base / "manifests"
        mdir.mkdir()
        (mdir / "demo-1.0").write_text(text)
        live = self.base / "live"
        (live / "usr" / "bin").mkdir(parents=True)
        (live / "usr" / "bin" / "demo").write_bytes(b"payload\n")
        db = PackageDB(str(self.base / "pkm.db"), root=str(live))
        try:
            self.assertEqual(db.import_manifests(mdir), 1)
            con = sqlite3.connect(str(db.db_path))
            rows = dict(con.execute("SELECT path, is_dir FROM files"))
            con.close()
        finally:
            db.close()
        self.assertEqual(rows["usr/bin"], 1)
        self.assertEqual(rows["usr"], 1)
        self.assertEqual(rows["usr/bin/demo"], 0)


class BashDriverReleaseThreadingTest(unittest.TestCase):
    """Every bash driver passes the recipe's release into pkg_install.

    The comments in pkg-functions.sh asserted the opposite for months — that no
    caller supplied it — which is the text a reviewer reads to judge whether the
    bash lane states its release at all. The comments are corrected; this pins
    the behaviour they describe so the two cannot drift apart again.
    """

    DRIVERS = ("chroot-build-ch8.sh", "chroot-build-base.sh",
               "chroot-build-ch10.sh", "chroot-build-core-extra.sh")

    def test_each_driver_threads_a_release_argument(self):
        for driver in self.DRIVERS:
            with self.subTest(driver=driver):
                text = (REPO_ROOT / "scripts" / driver).read_text()
                calls = [ln.strip() for ln in text.splitlines()
                         if ln.strip().startswith("pkg_install ")]
                self.assertTrue(calls, f"{driver} never calls pkg_install")
                for call in calls:
                    self.assertEqual(
                        len(call.split()), 5,
                        f"{driver}: pkg_install needs name, version, "
                        f"description and release — got: {call}")
                self.assertIn("get_package_release", text,
                              f"{driver} must derive the release from the recipe")


if __name__ == "__main__":
    unittest.main()

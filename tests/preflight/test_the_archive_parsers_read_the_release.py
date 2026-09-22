# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A reader that takes an archive filename apart must not mis-read the release.

The producers now write ``<name>-<version>-<release>.igos.tar.gz``. Two of the
readers that take those names apart stamp the version they derive INTO the
archive's own ``.PKGINFO``, which the repository index is then built from, so
a mis-read name becomes a wrong version served to every machine.

``scripts/backfill-pkginfo.py`` matched ``^(.+)-([0-9].*)$`` greedily, which
reads ``demo-1.0-7`` as name ``demo-1.0`` and version ``7``.
``scripts/inject-pkginfo.py`` took the longest recipe name and called
everything after it the version, which reads the same stem as version
``1.0-7``. Both are wrong in a way nothing downstream can detect.

These cases run the real scripts as real processes against a scratch archive
directory and read the ``.PKGINFO`` they stamp.
"""

import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_archive(path: Path, with_pkginfo: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = path.parent / "payload"
    payload.write_text("payload\n")
    with tarfile.open(path, "w:gz") as tar:
        tar.add(payload, arcname="./usr/bin/demo")
    payload.unlink()


def _pkginfo_of(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as tar:
        member = next((m for m in tar.getmembers()
                       if m.name.endswith(".PKGINFO")), None)
        if member is None:
            return {}
        text = tar.extractfile(member).read().decode()
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    return fields


class _ScratchTree(unittest.TestCase):
    """A scratch archive directory and a scratch recipe tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.archives = self.tmp / "archives"
        self.archives.mkdir()
        self.repo = self.tmp / "repo"
        recipe_dir = self.repo / "packages" / "core" / "demo"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "package.yml").write_text(
            "name: demo\n"
            "version: '1.0'\n"
            "release: 7\n"
            "description: a scratch package\n"
            "license: GPL-3.0-or-later\n"
            "tier: core\n"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, script, *args):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / script), *args],
            capture_output=True, text=True, timeout=120,
        )


class TestBackfillReadsTheRelease(_ScratchTree):
    def test_the_stamped_version_is_the_version_not_the_release(self):
        archive = self.archives / "demo-1.0-7.igos.tar.gz"
        _make_archive(archive)
        result = self._run("backfill-pkginfo.py",
                           "--archive-dir", str(self.archives),
                           "--repo-root", str(self.repo))
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)
        fields = _pkginfo_of(archive)
        self.assertEqual(fields.get("pkgname"), "demo",
                         f"the name was mis-read: {fields}")
        self.assertEqual(fields.get("pkgver"), "1.0",
                         f"the version was mis-read: {fields}")
        self.assertEqual(fields.get("pkgrel"), "7",
                         f"the release was mis-read: {fields}")


class TestInjectReadsTheRelease(_ScratchTree):
    def test_the_stamped_version_is_the_version_not_version_and_release(self):
        archive = self.archives / "demo-1.0-7.igos.tar.gz"
        _make_archive(archive)
        result = self._run("inject-pkginfo.py",
                           "--archive-dir", str(self.archives),
                           "--exclude-dir", str(self.tmp / "excluded"),
                           "--repo-root", str(self.repo))
        # This script is a loud DETECTOR: finding an archive without a
        # build-time .PKGINFO is itself a reported gate escape, so a non-zero
        # exit here is its correct behaviour and not what this case is about.
        # It still repacks the archive, and what it stamped is the subject.
        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        fields = _pkginfo_of(archive)
        self.assertEqual(fields.get("pkgname"), "demo",
                         f"the name was mis-read: {fields}")
        self.assertEqual(fields.get("pkgver"), "1.0",
                         f"the version was mis-read: {fields}")
        self.assertEqual(fields.get("pkgrel"), "7",
                         f"the release was mis-read: {fields}")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The served index names the file that is actually there.

The build now writes ``<name>-<version>-<release>.igos.tar.gz`` (decided
2026-09-22). The repository index's ``filename`` field is what a machine
appends to a mirror URL to download a package, so it has to be the name of the
archive the publisher actually placed on the mirror — and when two releases of
one version exist, it has to be the newer one.

These cases run the real index generator over a real archive directory and
read the field back out of the real signed-index shape.
"""

import gzip
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pkm.archive_names import archive_filename  # noqa: E402
from pkm.repo import generate_index  # noqa: E402


def _archive(directory: Path, name, version, release):
    path = directory / archive_filename(name, version, release)
    body = (f"pkgname={name}\npkgver={version}\npkgrel={release}\n"
            f"pkgdesc=a scratch package\nlicense=GPL-3.0-or-later\n"
            f"tier=core\n").encode()
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
        payload = b"payload\n"
        member = tarfile.TarInfo(f"./usr/bin/{name}")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    return path


def _entries(index_path: Path) -> dict:
    # The index is written gzip-compressed whatever its name; read it the way
    # pkm itself does rather than by guessing from the suffix.
    with gzip.open(index_path, "rb") as handle:
        doc = json.loads(handle.read().decode())
    packages = doc["packages"] if isinstance(doc, dict) and "packages" in doc else doc
    if isinstance(packages, dict):
        return packages
    return {p["name"]: p for p in packages}


class TestTheIndexFilenameFollowsTheArchive(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _index(self):
        out = self.dir / "InterGenOS.db"
        generate_index(self.dir, output=out)
        return _entries(out)

    def test_the_filename_field_carries_the_release(self):
        _archive(self.dir, "demo", "1.0", 7)
        entry = self._index()["demo"]
        self.assertEqual(entry["filename"], "demo-1.0-7.igos.tar.gz")
        self.assertEqual(entry["version"], "1.0")
        self.assertEqual(str(entry["release"]), "7")

    def test_two_releases_of_one_version_index_the_newer(self):
        older = _archive(self.dir, "demo", "1.0", 7)
        newer = _archive(self.dir, "demo", "1.0", 8)
        self.assertTrue(older.exists() and newer.exists())
        entry = self._index()["demo"]
        self.assertEqual(entry["filename"], "demo-1.0-8.igos.tar.gz")
        self.assertEqual(str(entry["release"]), "8")

    def test_a_release_less_archive_is_indexed_under_its_own_name(self):
        # Every archive published before this change carries the release-less
        # name; the index must keep naming the file that is on the mirror.
        path = self.dir / "legacy-2.0.igos.tar.gz"
        body = (b"pkgname=legacy\npkgver=2.0\npkgrel=3\n"
                b"pkgdesc=x\nlicense=GPL-3.0-or-later\ntier=core\n")
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo("./.PKGINFO")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        entry = self._index()["legacy"]
        self.assertEqual(entry["filename"], "legacy-2.0.igos.tar.gz")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository indexes select the newest package metadata, including releases."""

import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from pkm.repo import generate_index


def _write_archive(path, version, release, name="sample"):
    payload = f"payload for {path.name}\n".encode()
    pkginfo = (
        f"pkgname={name}\npkgver={version}\npkgrel={release}\n"
        f"pkgdesc={path.name}\nsize={len(payload)}\n"
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        for member_name, content in ((".PKGINFO", pkginfo),
                                     (f"usr/share/{name}.txt", payload)):
            member = tarfile.TarInfo(member_name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


class IndexVersionOrderTest(unittest.TestCase):
    def _assert_selected(self, candidates, expected_filename):
        with tempfile.TemporaryDirectory() as temporary:
            package_dir = Path(temporary)
            for filename, version, release in candidates:
                _write_archive(package_dir / filename, version, release)
            _write_archive(package_dir / "unrelated.igos.tar.gz", "99", 99,
                           name="unrelated")

            output = generate_index(package_dir)
            with gzip.open(output, "rt", encoding="utf-8") as stream:
                index = json.load(stream)

            self.assertEqual(index["package_count"], 2)
            self.assertEqual(set(index["packages"]), {"sample", "unrelated"})
            entry = index["packages"]["sample"]
            expected = package_dir / expected_filename
            _, version, release = next(
                candidate for candidate in candidates
                if candidate[0] == expected_filename
            )
            self.assertEqual((entry["version"], entry["release"]),
                             (version, release))
            self.assertEqual(entry["filename"], expected.name)
            self.assertEqual(entry["description"], expected.name)
            self.assertEqual(entry["size"], expected.stat().st_size)
            self.assertEqual(entry["sha256"],
                             hashlib.sha256(expected.read_bytes()).hexdigest())

    def test_numeric_version_order(self):
        self._assert_selected([
            ("sample-1.9-1.igos.tar.gz", "1.9", 1),
            ("sample-1.10-1.igos.tar.gz", "1.10", 1),
        ], "sample-1.10-1.igos.tar.gz")

    def test_numeric_release_order(self):
        self._assert_selected([
            ("sample-1.0-2.igos.tar.gz", "1.0", 2),
            ("sample-1.0-10.igos.tar.gz", "1.0", 10),
        ], "sample-1.0-10.igos.tar.gz")

    def test_version_precedes_release(self):
        self._assert_selected([
            ("sample-1.9-99.igos.tar.gz", "1.9", 99),
            ("sample-1.10-1.igos.tar.gz", "1.10", 1),
        ], "sample-1.10-1.igos.tar.gz")

    def test_newer_metadata_can_sort_first_by_filename(self):
        self._assert_selected([
            ("a-current.igos.tar.gz", "2.0", 1),
            ("z-old.igos.tar.gz", "1.0", 1),
        ], "a-current.igos.tar.gz")

    def test_newer_metadata_can_sort_last_by_filename(self):
        self._assert_selected([
            ("a-old.igos.tar.gz", "1.0", 1),
            ("z-current.igos.tar.gz", "2.0", 1),
        ], "z-current.igos.tar.gz")

    def test_upstream_build_counter_order(self):
        self._assert_selected([
            ("sample-b9-1.igos.tar.gz", "b9", 1),
            ("sample-b10-1.igos.tar.gz", "b10", 1),
        ], "sample-b10-1.igos.tar.gz")

    def test_final_version_follows_tilde_prerelease(self):
        self._assert_selected([
            ("sample-1.0~rc1-1.igos.tar.gz", "1.0~rc1", 1),
            ("sample-1.0-1.igos.tar.gz", "1.0", 1),
        ], "sample-1.0-1.igos.tar.gz")

    def test_equal_version_and_release_keep_existing_filename_tie_order(self):
        self._assert_selected([
            ("a-copy.igos.tar.gz", "1.0", 1),
            ("z-copy.igos.tar.gz", "1.0", 1),
        ], "z-copy.igos.tar.gz")


if __name__ == "__main__":
    unittest.main()

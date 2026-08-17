#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A13 regression: _read_package_meta in-process read + loud-on-corruption.

The pre-A13 reader shelled `tar` twice (a .PKGINFO probe then an
always-dead metadata.json probe) inside `except Exception: pass`, so a
corrupt or tampered archive read as "no metadata" (None) and was then
silently dropped from the signed index (generate_index) or installed
with empty metadata + release=1 (installer). These tests pin the fixed
contract:

  * a valid archive with .PKGINFO is read in-process and parsed;
  * a valid archive with NO .PKGINFO returns None (the legitimate
    pre-ratification fallback — NOT corruption);
  * the dead metadata.json probe is gone (a metadata.json-only archive
    reads as None, not as JSON);
  * a corrupt / truncated / undecodable / missing archive raises
    ArchiveReadError (loud, fail-closed) instead of swallowing to None;
  * generate_index fails closed on a corrupt archive in the set rather
    than silently dropping it from the index;
  * installer.install aborts fail-closed on a corrupt archive with a
    legible re-fetch message (no empty-metadata deploy).
"""

import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pkm.repo import (
    ArchiveReadError,
    _read_package_meta,
    generate_index,
)
from pkm.installer import PackageInstaller


def _write_pkginfo_archive(path, name, version, pkgrel=None, member=".PKGINFO"):
    """Valid .igos.tar.gz with a .PKGINFO at `member` + one payload file."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lines = [f"pkgname={name}", f"pkgver={version}"]
        if pkgrel is not None:
            lines.append(f"pkgrel={pkgrel}")
        lines += ["pkgdesc=test pkg", "license=GPL", "tier=core",
                  "builddate=2026-06-16T00:00:00Z", "size=8", "filecount=1"]
        pkginfo = tmp / "PKGINFO.txt"
        pkginfo.write_text("\n".join(lines) + "\n")
        payload = tmp / "payload.txt"
        payload.write_text("payload\n")
        with tarfile.open(path, "w:gz") as tf:
            tf.add(pkginfo, arcname=member)
            tf.add(payload, arcname=f"usr/{name}.txt")


def _write_payload_only_archive(path, member="usr/foo.txt"):
    """Valid .igos.tar.gz that opens cleanly but carries no .PKGINFO."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "foo.txt"
        f.write_text("nothing useful\n")
        with tarfile.open(path, "w:gz") as tf:
            tf.add(f, arcname=member)


class ReadPackageMetaTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_reads_pkginfo_in_process(self):
        arc = self.tmp / "good-1.0.0.igos.tar.gz"
        _write_pkginfo_archive(arc, "good", "1.0.0", pkgrel=5)
        meta = _read_package_meta(arc)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "good")
        self.assertEqual(meta["version"], "1.0.0")
        self.assertEqual(meta["release"], 5)  # parsed int (A02/A06 chain)

    def test_reads_pkginfo_at_dot_slash_prefix(self):
        # Archives may store the member as ./.PKGINFO — basename match.
        arc = self.tmp / "dot-1.0.igos.tar.gz"
        _write_pkginfo_archive(arc, "dot", "1.0", member="./.PKGINFO")
        meta = _read_package_meta(arc)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "dot")

    def test_no_pkginfo_returns_none(self):
        # Opens cleanly, no .PKGINFO member -> legitimate "no metadata".
        arc = self.tmp / "bare.igos.tar.gz"
        _write_payload_only_archive(arc)
        self.assertIsNone(_read_package_meta(arc))

    def test_metadata_json_probe_is_dead(self):
        # The old reader fell back to *metadata.json; that probe is gone,
        # so an archive carrying only metadata.json reads as None.
        arc = self.tmp / "jsononly.igos.tar.gz"
        with tempfile.TemporaryDirectory() as src:
            mj = Path(src) / "metadata.json"
            mj.write_text(json.dumps({"name": "ghost", "version": "9.9"}))
            with tarfile.open(arc, "w:gz") as tf:
                tf.add(mj, arcname="metadata.json")
        self.assertIsNone(_read_package_meta(arc))

    def test_corrupt_archive_raises_loud(self):
        # Garbage bytes named like an archive: the pre-A13 reader returned
        # None (silent); now it raises ArchiveReadError.
        arc = self.tmp / "corrupt-1.0.igos.tar.gz"
        arc.write_bytes(b"this is not a gzip tar at all\x00\x01\x02")
        with self.assertRaises(ArchiveReadError):
            _read_package_meta(arc)

    def test_truncated_gzip_raises_loud(self):
        good = self.tmp / "whole-1.0.igos.tar.gz"
        _write_pkginfo_archive(good, "whole", "1.0", pkgrel=1)
        raw = good.read_bytes()
        trunc = self.tmp / "trunc-1.0.igos.tar.gz"
        trunc.write_bytes(raw[: len(raw) // 2])  # half a gzip stream
        with self.assertRaises(ArchiveReadError):
            _read_package_meta(trunc)

    def test_missing_file_raises_loud(self):
        with self.assertRaises(ArchiveReadError):
            _read_package_meta(self.tmp / "does-not-exist.igos.tar.gz")


class GenerateIndexFailClosedTest(unittest.TestCase):

    def test_generate_index_fails_closed_on_corrupt_archive(self):
        # A corrupt archive in the publish set must HALT index generation
        # (the index is the signed manifest of the whole repo) — never be
        # silently omitted from the signed index.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_pkginfo_archive(d / "good-1.0.0.igos.tar.gz",
                                   "good", "1.0.0", pkgrel=1)
            (d / "bad-2.0.0.igos.tar.gz").write_bytes(b"corrupt\xff\xfe")
            with self.assertRaises(ArchiveReadError):
                generate_index(d, output=d / "InterGenOS.db")

    def test_generate_index_clean_set_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_pkginfo_archive(d / "a-1.0.igos.tar.gz", "a", "1.0", pkgrel=1)
            _write_pkginfo_archive(d / "b-2.0.igos.tar.gz", "b", "2.0", pkgrel=3)
            out = generate_index(d, output=d / "InterGenOS.db")
            self.assertTrue(Path(out).exists())


class InstallerFailClosedTest(unittest.TestCase):

    def test_install_aborts_on_corrupt_archive(self):
        db = MagicMock()
        db.get_installed.return_value = None
        installer = PackageInstaller(db=db)
        with tempfile.TemporaryDirectory() as tmp:
            arc = Path(tmp) / "evil-1.0.igos.tar.gz"
            arc.write_bytes(b"not a real archive \x00\x01")
            ok, msg = installer.install("evil", archive_path=arc)
            self.assertFalse(ok)
            self.assertIn("corrupt", msg.lower())
            self.assertIn("evil", msg)
            # Fail-closed BEFORE any deploy/DB write.
            db.add_installed.assert_not_called()


if __name__ == "__main__":
    unittest.main()

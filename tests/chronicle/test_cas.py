#!/usr/bin/env python3
"""Content-addressed store: put/get, sha verification, scrub, and GC (a blob is
dropped only when unreferenced)."""

import tempfile
import unittest
from pathlib import Path

from chronicle import cas as _cas


class CasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-cas-")
        self.store = _cas.ContentStore(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_put_get_roundtrip_and_dedup(self):
        sha = self.store.put_bytes(b"hello")
        self.assertTrue(self.store.exists(sha))
        self.assertEqual(self.store.read_bytes(sha), b"hello")
        # Same content -> same address (dedup).
        sha2 = self.store.put_bytes(b"hello")
        self.assertEqual(sha, sha2)

    def test_verify_detects_tamper(self):
        sha = self.store.put_bytes(b"payload")
        self.assertTrue(self.store.verify(sha))
        self.store.blob_path(sha).write_bytes(b"tampered")
        self.assertFalse(self.store.verify(sha))

    def test_scrub_lists_corrupt(self):
        good = self.store.put_bytes(b"good")
        bad = self.store.put_bytes(b"bad-original")
        self.assertEqual(self.store.scrub(), [])
        self.store.blob_path(bad).write_bytes(b"flipped")
        corrupt = self.store.scrub()
        self.assertEqual(corrupt, [bad])
        self.assertNotIn(good, corrupt)

    def test_gc_drops_only_unreferenced(self):
        keep = self.store.put_bytes(b"referenced")
        drop = self.store.put_bytes(b"orphan")
        deleted = self.store.gc({keep})
        self.assertEqual(deleted, [drop])
        self.assertTrue(self.store.exists(keep))
        self.assertFalse(self.store.exists(drop))

    def test_put_file(self):
        p = Path(self.tmp) / "src"
        p.write_bytes(b"filedata")
        sha = self.store.put_file(p)
        self.assertEqual(self.store.read_bytes(sha), b"filedata")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""User-data captures preserve the bytes observed for each version."""

import os
import tempfile
import unittest
from pathlib import Path

from chronicle import engine as _engine
from chronicle import paths as _paths
from chronicle import userdata as _userdata


class UserDataCaptureIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-userdata-")
        root = Path(self.tmp.name)
        self.source = root / "source"
        self.source.mkdir()
        self.document = self.source / "document"
        self.target = root / "target"
        self.target.mkdir()
        self.engine = _engine.Engine(
            local_root=root / "local", now_fn=lambda: 1_000_000
        )
        self.engine.config.user_data_paths = [str(self.source)]
        self.engine.target_adopt(self.target, target_class="directory")

    def tearDown(self):
        self.tmp.cleanup()

    def _capture(self, reason):
        return self.engine.capture(
            _paths.LAYER_USER_DATA, reason=reason
        )["version_id"]

    def _stored_document(self, version):
        captured = self.engine.get_manifest(_paths.LAYER_USER_DATA, version)
        entry = next(
            item for item in captured["entries"]
            if item["path"] == str(self.document)
        )
        return _userdata.read_file(self.engine.target_root(), version, entry)

    def test_same_size_edit_within_one_second_gets_new_stored_bytes(self):
        second = 1_800_000_000_000_000_000
        self.document.write_bytes(b"AAAA")
        os.utime(self.document, ns=(second + 100, second + 100))
        first = self._capture("first bytes")

        self.document.write_bytes(b"BBBB")
        os.utime(self.document, ns=(second + 900, second + 900))
        second_version = self._capture("changed within one second")

        first_copy = self._stored_document(first)
        second_copy = self._stored_document(second_version)
        self.assertEqual(first_copy.read_bytes(), b"AAAA")
        self.assertEqual(second_copy.read_bytes(), b"BBBB")
        self.assertNotEqual(
            first_copy.stat().st_ino,
            second_copy.stat().st_ino,
            "different bytes must not reuse the earlier hardlink",
        )
        self.assertTrue(self.engine.verify(_paths.LAYER_USER_DATA, first)["ok"])
        self.assertTrue(
            self.engine.verify(_paths.LAYER_USER_DATA, second_version)["ok"]
        )


if __name__ == "__main__":
    unittest.main()

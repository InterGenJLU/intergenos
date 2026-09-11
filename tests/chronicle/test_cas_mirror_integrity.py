#!/usr/bin/env python3
"""Mirroring refuses corrupt target objects before committing a manifest."""

import tempfile
import unittest
from pathlib import Path

from chronicle import cas as _cas
from chronicle import engine as _engine
from chronicle import manifest as _manifest
from chronicle import paths as _paths


class CasMirrorIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-mirror-")
        root = Path(self.tmp.name)
        self.local = root / "local"
        self.target = root / "target"
        self.target.mkdir()
        self.source = root / "source"
        self.source.mkdir()
        self.document = self.source / "setting"
        self.document.write_bytes(b"mirrored bytes")
        self.engine = _engine.Engine(local_root=self.local)
        self.engine.target_adopt(self.target, target_class="directory")
        self.target_root = self.engine.target_root()
        self.target_store = _cas.ContentStore(self.target_root)
        self.sha = _cas.sha256_file(self.document)

    def tearDown(self):
        self.tmp.cleanup()

    def _preseed_target(self, data):
        path = self.target_store.blob_path(self.sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _capture(self):
        return self.engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(self.source)],
            reason="mirror integrity",
        )["version_id"]

    def test_corrupt_existing_target_blob_blocks_manifest_commit(self):
        self._preseed_target(b"corrupt")

        with self.assertRaises(_cas.CorruptBlob):
            self._capture()

        self.assertEqual(
            _manifest.list_versions(
                self.target_root, _paths.LAYER_CONFIG_STATE
            ),
            [],
            "a target manifest must not reference the rejected blob",
        )
        self.assertEqual(self.target_store.blob_path(self.sha).read_bytes(), b"corrupt")

    def test_healthy_existing_target_blob_can_be_mirrored(self):
        self._preseed_target(b"mirrored bytes")

        version = self._capture()

        mirrored = _manifest.find_version(
            self.target_root, _paths.LAYER_CONFIG_STATE, version
        )
        self.assertIsNotNone(mirrored)
        ok, problems = _manifest.verify_version(
            self.target_root, mirrored, self.target_store
        )
        self.assertTrue(ok, problems)


if __name__ == "__main__":
    unittest.main()

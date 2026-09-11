#!/usr/bin/env python3
"""Mirroring refuses corrupt target objects before committing a manifest."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_persistent_mirror_failure_does_not_accumulate_local_versions(self):
        self._preseed_target(b"corrupt")

        for _attempt in range(3):
            with self.assertRaises(_cas.CorruptBlob):
                self._capture()

        local_versions = _manifest.list_versions(
            self.local, _paths.LAYER_CONFIG_STATE
        )
        target_versions = _manifest.list_versions(
            self.target_root, _paths.LAYER_CONFIG_STATE
        )
        self.assertEqual(local_versions, [])
        self.assertEqual(target_versions, [])
        self.assertEqual(list(self.engine.local_store.iter_blobs()), [])
        self.assertEqual(
            self.target_store.blob_path(self.sha).read_bytes(), b"corrupt"
        )

        self.target_store.blob_path(self.sha).unlink()
        recovered = self._capture()
        local_versions = _manifest.list_versions(
            self.local, _paths.LAYER_CONFIG_STATE
        )
        target_versions = _manifest.list_versions(
            self.target_root, _paths.LAYER_CONFIG_STATE
        )
        self.assertEqual(
            [manifest["version_id"] for manifest in local_versions],
            [recovered],
        )
        self.assertEqual(
            [manifest["version_id"] for manifest in target_versions],
            [recovered],
        )
        ok, problems = _manifest.verify_version(
            self.target_root, target_versions[0], self.target_store
        )
        self.assertTrue(ok, problems)

    def test_failed_mirror_preserves_prior_local_history(self):
        first = self._capture()
        first_sha = self.sha
        changed = b"changed mirrored bytes"
        self.document.write_bytes(changed)
        changed_sha = _cas.sha256_bytes(changed)
        corrupt = self.target_store.blob_path(changed_sha)
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_bytes(b"corrupt")

        with self.assertRaises(_cas.CorruptBlob):
            self._capture()

        local_versions = _manifest.list_versions(
            self.local, _paths.LAYER_CONFIG_STATE
        )
        target_versions = _manifest.list_versions(
            self.target_root, _paths.LAYER_CONFIG_STATE
        )
        self.assertEqual(
            [manifest["version_id"] for manifest in local_versions], [first]
        )
        self.assertEqual(
            [manifest["version_id"] for manifest in target_versions], [first]
        )
        self.assertTrue(self.engine.local_store.exists(first_sha))
        self.assertFalse(self.engine.local_store.exists(changed_sha))
        self.assertEqual(corrupt.read_bytes(), b"corrupt")

    def test_partial_target_copy_is_removed_when_a_later_blob_is_corrupt(self):
        extra = self.source / "second"
        extra.write_bytes(b"second mirrored value")
        self.engine.state["target"] = None
        self.engine._save_state()
        local_version = self._capture()
        self.engine.target_adopt(self.target, target_class="directory")
        local_manifest = _manifest.find_version(
            self.local, _paths.LAYER_CONFIG_STATE, local_version
        )
        files = [
            entry for entry in local_manifest["entries"]
            if entry.get("type") == _manifest.T_FILE
        ]
        self.assertEqual(len(files), 2)
        copied_sha = files[0]["sha256"]
        corrupt_sha = files[1]["sha256"]
        unrelated_sha = self.target_store.put_bytes(b"unrelated target evidence")
        corrupt = self.target_store.blob_path(corrupt_sha)
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_bytes(b"corrupt")

        with self.assertRaises(_cas.CorruptBlob):
            self.engine._mirror_to_target(
                _paths.LAYER_CONFIG_STATE, local_version
            )

        self.assertFalse(self.target_store.exists(copied_sha))
        self.assertEqual(corrupt.read_bytes(), b"corrupt")
        self.assertTrue(self.target_store.exists(unrelated_sha))
        self.assertEqual(
            _manifest.list_versions(
                self.target_root, _paths.LAYER_CONFIG_STATE
            ),
            [],
        )

    def test_changed_local_blob_preserves_preexisting_target_alias(self):
        self.engine.state["target"] = None
        self.engine._save_state()
        local_version = self._capture()
        changed = b"different bytes already present at the target"
        changed_sha = self.target_store.put_bytes(changed)
        self.engine.local_store.blob_path(self.sha).write_bytes(changed)
        self.engine.target_adopt(self.target, target_class="directory")

        with self.assertRaisesRegex(_cas.CorruptBlob, "source blob changed"):
            self.engine._mirror_to_target(
                _paths.LAYER_CONFIG_STATE, local_version
            )

        self.assertTrue(self.target_store.exists(changed_sha))
        self.assertEqual(
            self.target_store.read_bytes(changed_sha), changed
        )
        self.assertEqual(
            _manifest.list_versions(
                self.target_root, _paths.LAYER_CONFIG_STATE
            ),
            [],
        )

    def test_local_rollback_cleanup_failure_is_reported(self):
        self._preseed_target(b"corrupt")
        local_blob = self.engine.local_store.blob_path(self.sha)
        real_unlink = Path.unlink

        def refuse_local_blob(path, *args, **kwargs):
            if path == local_blob:
                raise PermissionError("injected local blob refusal")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", new=refuse_local_blob):
            with self.assertRaisesRegex(
                _engine.EngineError, "local capture rollback failed"
            ):
                self._capture()

        self.assertEqual(
            _manifest.list_versions(self.local, _paths.LAYER_CONFIG_STATE), []
        )
        self.assertTrue(self.engine.local_store.exists(self.sha))

    def test_post_commit_cap_failure_rolls_back_both_manifests(self):
        with mock.patch.object(
            self.engine,
            "_enforce_directory_target_cap",
            side_effect=_engine.EngineError("injected cap failure"),
        ):
            with self.assertRaisesRegex(_engine.EngineError, "injected cap failure"):
                self._capture()

        self.assertEqual(
            _manifest.list_versions(self.local, _paths.LAYER_CONFIG_STATE), []
        )
        self.assertEqual(
            _manifest.list_versions(
                self.target_root, _paths.LAYER_CONFIG_STATE
            ),
            [],
        )
        self.assertEqual(list(self.engine.local_store.iter_blobs()), [])
        self.assertEqual(list(self.target_store.iter_blobs()), [])

    def test_cap_rejection_removes_preexisting_candidate_alias(self):
        baseline = _engine._allocated_bytes(self.target_root)
        self._preseed_target(b"mirrored bytes")
        self.engine.state["target"]["cap_bytes"] = baseline
        self.engine._save_state()

        with self.assertRaisesRegex(_engine.EngineError, "directory target cap"):
            self._capture()

        self.assertEqual(
            _manifest.list_versions(self.local, _paths.LAYER_CONFIG_STATE), []
        )
        self.assertEqual(
            _manifest.list_versions(
                self.target_root, _paths.LAYER_CONFIG_STATE
            ),
            [],
        )
        self.assertFalse(self.target_store.exists(self.sha))
        self.assertLessEqual(
            _engine._allocated_bytes(self.target_root), baseline
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Reclamation stops when committed manifests cannot be inventoried."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chronicle import engine as _engine
from chronicle import manifest as _manifest
from chronicle import paths as _paths


class ManifestReclamationGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-manifest-")
        self.root = Path(self.tmp.name) / "store"
        self.source = Path(self.tmp.name) / "source"
        self.source.mkdir()
        (self.source / "kept.conf").write_text("keep these bytes\n")
        self.engine = _engine.Engine(local_root=self.root)
        self.version = self.engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(self.source)],
            reason="reclamation guard",
        )["version_id"]
        captured = self.engine.get_manifest(
            _paths.LAYER_CONFIG_STATE, self.version
        )
        self.wall_clock = captured["wall_clock"]
        entry = next(
            item for item in captured["entries"]
            if item["path"] == str(self.source / "kept.conf")
        )
        self.blob = self.engine.local_store.blob_path(entry["sha256"])
        self.manifest_path = Path(captured["_path"])

    def tearDown(self):
        self.tmp.cleanup()

    def _assert_reclamation_stops_and_preserves_blob(self):
        with self.assertRaisesRegex(
            _engine.EngineError, "manifest inventory is incomplete"
        ):
            self.engine.retention_apply(_paths.LAYER_CONFIG_STATE)
        self.assertTrue(
            self.blob.exists(),
            "reclamation must not delete data when references are unknown",
        )

    def test_malformed_manifest_stops_reclamation(self):
        self.manifest_path.write_text("{not valid json", encoding="utf-8")
        self._assert_reclamation_stops_and_preserves_blob()

    def test_unreadable_manifest_stops_reclamation(self):
        real_load = _manifest.load_manifest

        def unreadable(path):
            if Path(path) == self.manifest_path:
                raise PermissionError("injected read failure")
            return real_load(path)

        with mock.patch.object(_manifest, "load_manifest", side_effect=unreadable):
            self._assert_reclamation_stops_and_preserves_blob()

    def test_wrong_shaped_manifest_stops_reclamation(self):
        self.manifest_path.write_text("[]", encoding="utf-8")
        self._assert_reclamation_stops_and_preserves_blob()

    def test_manifest_unlink_failure_stops_before_blob_collection(self):
        (self.source / "kept.conf").write_text("new bytes\n")
        self.engine._now_fn = lambda: self.wall_clock + 60
        self.engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(self.source)],
            reason="newer version",
        )
        self.engine._now_fn = lambda: self.wall_clock + 800 * 86_400

        versions_dir = self.manifest_path.parent
        versions_dir.chmod(0o500)
        try:
            with self.assertRaisesRegex(
                _engine.EngineError, "could not remove manifest"
            ):
                self.engine.retention_apply(_paths.LAYER_CONFIG_STATE)
        finally:
            if versions_dir.exists():
                versions_dir.chmod(0o700)
        self.assertTrue(
            self.blob.exists(),
            "garbage collection must not run after manifest removal fails",
        )


if __name__ == "__main__":
    unittest.main()

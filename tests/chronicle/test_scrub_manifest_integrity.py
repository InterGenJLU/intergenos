#!/usr/bin/env python3
"""Scrub validates committed manifests and every referenced object."""

import json
import tempfile
import unittest
from pathlib import Path

from chronicle import cas as _cas
from chronicle import engine as _engine
from chronicle import paths as _paths


class ScrubManifestIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-scrub-")
        root = Path(self.tmp.name)
        self.local = root / "local"
        self.source = root / "config"
        self.source.mkdir()
        self.document = self.source / "setting"
        self.document.write_bytes(b"shared bytes")
        self.engine = _engine.Engine(
            local_root=self.local, now_fn=lambda: 1_000_000
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _capture_config(self, reason):
        return self.engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(self.source)],
            reason=reason,
        )["version_id"]

    def _manifest(self, layer, version):
        return self.engine.get_manifest(layer, version)

    def _file_entry(self, manifest, path):
        return next(item for item in manifest["entries"] if item["path"] == str(path))

    def test_missing_blob_names_every_referencing_version(self):
        first = self._capture_config("first")
        second = self._capture_config("second")
        entry = self._file_entry(
            self._manifest(_paths.LAYER_CONFIG_STATE, first), self.document
        )
        self.engine.local_store.blob_path(entry["sha256"]).unlink()

        report = self.engine.scrub()

        missing = [
            item for item in report["corrupt"]
            if item.get("kind") == "missing-blob"
        ]
        self.assertFalse(report["clean"])
        self.assertEqual(len(missing), 1, report)
        self.assertEqual(missing[0]["sha256"], entry["sha256"])
        self.assertEqual(missing[0]["versions"], sorted([first, second]))

    def test_malformed_manifest_is_reported(self):
        version = self._capture_config("malformed")
        manifest_path = Path(
            self._manifest(_paths.LAYER_CONFIG_STATE, version)["_path"]
        )
        manifest_path.write_text("{not json", encoding="utf-8")

        report = self.engine.scrub()

        problems = [
            item for item in report["corrupt"]
            if item.get("kind") == "manifest-unreadable"
        ]
        self.assertFalse(report["clean"])
        self.assertEqual(len(problems), 1, report)
        self.assertEqual(Path(problems[0]["path"]), manifest_path)

    def test_manifest_root_hash_mismatch_is_reported(self):
        version = self._capture_config("root hash")
        captured = self._manifest(_paths.LAYER_CONFIG_STATE, version)
        manifest_path = Path(captured["_path"])
        body = json.loads(manifest_path.read_text(encoding="utf-8"))
        body["entries"][0]["mode"] ^= 0o001
        manifest_path.write_text(json.dumps(body), encoding="utf-8")

        report = self.engine.scrub()

        problems = [
            item for item in report["corrupt"]
            if item.get("kind") == "manifest-root-hash"
        ]
        self.assertFalse(report["clean"])
        self.assertEqual(len(problems), 1, report)
        self.assertEqual(problems[0]["versions"], [version])

    def test_structurally_invalid_user_data_manifest_is_reported(self):
        version = "0000000001-aaaaaaaaaaaa"
        manifest_path = (
            _paths.versions_dir(self.local, _paths.LAYER_USER_DATA)
            / f"{version}.json"
        )
        manifest_path.write_text(json.dumps({
            "chronicle_manifest_version": 1,
            "layer": _paths.LAYER_USER_DATA,
            "sequence": 1,
            "wall_clock": 1_000_000,
            "reason": "invalid entry",
            "entries": [{"type": "file"}],
            "root_hash": "invalid",
            "version_id": version,
        }), encoding="utf-8")

        report = self.engine.scrub()

        problems = [
            item for item in report["corrupt"]
            if item.get("kind") == "manifest-invalid"
        ]
        self.assertFalse(report["clean"])
        self.assertEqual(len(problems), 1, report)
        self.assertEqual(Path(problems[0]["path"]), manifest_path)

    def test_cas_failure_does_not_name_tree_backed_versions(self):
        target = Path(self.tmp.name) / "target"
        target.mkdir()
        self.engine.target_adopt(target, target_class="directory")
        config_version = self._capture_config("mirrored config")

        user_root = Path(self.tmp.name) / "user"
        user_root.mkdir()
        user_file = user_root / "document"
        user_file.write_bytes(b"shared bytes")
        self.engine.config.user_data_paths = [str(user_root)]
        user_version = self.engine.capture(
            _paths.LAYER_USER_DATA, reason="tree backed"
        )["version_id"]

        config_manifest = self._manifest(
            _paths.LAYER_CONFIG_STATE, config_version
        )
        sha = self._file_entry(config_manifest, self.document)["sha256"]
        self.assertEqual(sha, _cas.sha256_file(user_file))
        target_store = _cas.ContentStore(self.engine.target_root())
        target_store.blob_path(sha).write_bytes(b"corrupt")

        report = self.engine.scrub()

        item = next(
            finding for finding in report["corrupt"]
            if finding.get("kind") == "corrupt-blob"
            and finding.get("store") == str(self.engine.target_root())
            and finding.get("sha256") == sha
        )
        self.assertEqual(item["versions"], [config_version])
        self.assertNotIn(user_version, item["versions"])


if __name__ == "__main__":
    unittest.main()

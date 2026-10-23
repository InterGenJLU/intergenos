#!/usr/bin/env python3
"""The scheduled scrub process exits nonzero after detecting corruption."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from chronicle import engine as _engine
from chronicle import paths as _paths


class ChronicledScrubStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-scrub-status-")
        root = Path(self.tmp.name)
        self.local = root / "local"
        self.source = root / "source"
        self.source.mkdir()
        self.document = self.source / "setting"
        self.document.write_text("intact\n")
        self.config = root / "chronicle.conf"
        self.config.write_text(
            "[chronicle]\nuser_data_paths = " + str(self.source) + "\n"
        )
        self.target = root / "target"
        self.script = (
            Path(__file__).resolve().parents[2]
            / "assets" / "intergenos-backup" / "chronicled"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _run_scrub(self):
        return subprocess.run(
            [sys.executable, str(self.script), "--local-root", str(self.local),
             "--config", str(self.config),
             "--task", "scrub"],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_clean_scrub_exits_zero(self):
        result = self._run_scrub()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["clean"])

    def test_corrupt_scrub_exits_nonzero_after_printing_report(self):
        engine = _engine.Engine(local_root=self.local, config_path=self.config)
        version = engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(self.source)],
            reason="scrub status",
        )["version_id"]
        manifest = engine.get_manifest(_paths.LAYER_CONFIG_STATE, version)
        entry = next(
            item for item in manifest["entries"]
            if item["path"] == str(self.document)
        )
        engine.local_store.blob_path(entry["sha256"]).unlink()

        result = self._run_scrub()

        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["clean"])
        self.assertEqual(report["corrupt"][0]["kind"], "missing-blob")

    def _capture_with_target(self):
        engine = _engine.Engine(local_root=self.local, config_path=self.config)
        self.assertEqual(engine.config.user_data_paths, [str(self.source)])
        self.target.mkdir()
        engine.target_adopt(str(self.target), target_class="directory")
        version = engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(self.source)],
            reason="scrub target control",
        )["version_id"]
        return engine, version

    def test_attached_target_scrub_exits_zero(self):
        self._capture_with_target()
        result = self._run_scrub()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["clean"])
        self.assertNotIn("target_refused", report)

    def _check_refused_target(self, corrupt_local):
        engine, version = self._capture_with_target()
        if corrupt_local:
            manifest = engine.get_manifest(_paths.LAYER_CONFIG_STATE, version)
            entry = next(item for item in manifest["entries"]
                         if item["path"] == str(self.document))
            engine.local_store.blob_path(entry["sha256"]).unlink()
        self.target.chmod(0o775)
        result = self._run_scrub()
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(report["clean"], not corrupt_local)
        self.assertIn("is refused", report["target_refused"])
        self.assertIn(
            "WARNING: Backup target was not checked: " + report["target_refused"],
            result.stderr,
        )
        if not corrupt_local:
            self.assertTrue(engine.verify(_paths.LAYER_CONFIG_STATE, version)["ok"])

    def test_refused_target_exits_nonzero_after_local_scrub(self):
        self._check_refused_target(corrupt_local=False)

    def test_refused_target_is_named_even_when_local_scrub_finds_corruption(self):
        self._check_refused_target(corrupt_local=True)


if __name__ == "__main__":
    unittest.main()

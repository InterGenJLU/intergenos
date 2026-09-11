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
        self.script = (
            Path(__file__).resolve().parents[2]
            / "assets" / "intergenos-backup" / "chronicled"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _run_scrub(self):
        return subprocess.run(
            [sys.executable, str(self.script), "--local-root", str(self.local),
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
        engine = _engine.Engine(local_root=self.local)
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


if __name__ == "__main__":
    unittest.main()

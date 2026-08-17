"""Regression tests for _remove_failed_tracking_artifacts.

The name-version manifest under pkg_db doubles as the --skip-built
completion marker. A build that fails AFTER pkg_manifest wrote it must not
leave it behind, or the next --skip-built run records the failed package as
skipped-successful and the failed gate never re-runs. The sealed archive is
quarantined (renamed out of the *.igos.tar.gz namespace) rather than
deleted, because pkg_deploy's failure text points operators at it for
manual recovery.
"""

import importlib
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

# Import the LIVE module from the worktree this test file lives in — never a
# hardcoded absolute root (a hardcoded root silently tests the wrong tree
# when running from a second worktree).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package  # noqa: E402
_builder_mod = importlib.import_module("igos-build.builder")
BuildExecutor = _builder_mod.BuildExecutor


class _CapturingLogger:
    def __init__(self):
        self.errors = []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        pass


def _make_stub(tmp: Path):
    """Minimal stand-in carrying only what the helper reads."""
    pkg_db = tmp / "packages"
    pkg_archives = tmp / "archives"
    pkg_db.mkdir()
    pkg_archives.mkdir()
    return SimpleNamespace(
        pkg_db=pkg_db,
        pkg_archives=pkg_archives,
        logger=_CapturingLogger(),
    )


class TestRemoveFailedTrackingArtifacts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.stub = _make_stub(self.tmp)
        self.pkg = make_package()

    def tearDown(self):
        self._tmp.cleanup()

    def _call(self):
        BuildExecutor._remove_failed_tracking_artifacts(self.stub, self.pkg)

    def test_manifest_removed(self):
        manifest = self.stub.pkg_db / "demo-1.0"
        manifest.write_text("PACKAGE NAME: demo-1.0\nTEMPLATE_HASH: abc\n")
        self._call()
        self.assertFalse(manifest.exists(),
                         "failed build left the --skip-built completion marker")

    def test_archive_quarantined_not_deleted(self):
        archive = self.stub.pkg_archives / "demo-1.0.igos.tar.gz"
        archive.write_bytes(b"payload")
        self._call()
        self.assertFalse(archive.exists(),
                         "unverified archive left in the ship namespace")
        quarantined = self.stub.pkg_archives / "demo-1.0.igos.tar.gz.failed"
        self.assertTrue(quarantined.exists(),
                        "recovery artifact was deleted instead of quarantined")
        self.assertEqual(quarantined.read_bytes(), b"payload")

    def test_noop_when_nothing_written(self):
        # Failure before pkg_manifest: nothing to clean, nothing raises.
        self._call()
        self.assertEqual(list(self.stub.pkg_db.iterdir()), [])
        self.assertEqual(list(self.stub.pkg_archives.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

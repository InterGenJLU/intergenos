#!/usr/bin/env python3
"""Tests for Phase 4 installer supersede behavior (RFC §4 / Phase 4 §2b-§2d).

Covers:
  - read_staged_manifest: SUPERSEDES parsing, hash parsing, legacy format
  - validate_predecessors: queue-order enforcement, missing, already-superseded
  - build_hash_map: primary from manifest, fallback from staged tree
  - already-installed + already-superseded refusal
  - install-order invariant enforcement
"""

import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


class TestReadStagedManifest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "root"
        self.root.mkdir()
        self.db_path = Path(self.tmp) / "test.db"
        self.db = PackageDB(self.db_path)
        self.installer = PackageInstaller(self.db, root=str(self.root))

    def tearDown(self):
        self.db.close()

    def _stage_manifest(self, name, content):
        staging = Path(self.tmp) / "staging"
        staging.mkdir(exist_ok=True)
        mdir = staging / "var" / "lib" / "igos" / "packages"
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / f"{name}-1.0").write_text(content)
        return staging

    def test_supersedes_parsed(self):
        staging = self._stage_manifest("test-pkg", """\
SUPERSEDES: old-pkg
FILE LIST:
fileA sha256:abcd1234
fileB
""")
        decl, files, hashes = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertEqual(decl, ["old-pkg"])
        self.assertEqual(files, ["fileA", "fileB"])
        self.assertEqual(hashes, {"fileA": "abcd1234"})

    def test_multiple_supersedes(self):
        staging = self._stage_manifest("test-pkg", """\
SUPERSEDES: a,b,c
FILE LIST:
file
""")
        decl, _, _ = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertEqual(decl, ["a", "b", "c"])

    def test_no_supersedes(self):
        staging = self._stage_manifest("test-pkg", """\
FILE LIST:
file
""")
        decl, _, _ = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertIsNone(decl)

    def test_no_manifest(self):
        staging = Path(self.tmp) / "empty-staging"
        staging.mkdir(exist_ok=True)
        decl, files, hashes = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertIsNone(decl)
        self.assertEqual(files, [])
        self.assertEqual(hashes, {})

    def test_legacy_format_no_hashes(self):
        staging = self._stage_manifest("test-pkg", """\
SUPERSEDES: old
FILE LIST:
plain-file
""")
        _, _, hashes = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertEqual(hashes, {})

    def test_mixed_hashes(self):
        staging = self._stage_manifest("test-pkg", """\
FILE LIST:
hashed sha256:ffff
unhashed
""")
        _, _, hashes = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertEqual(hashes, {"hashed": "ffff"})

    def test_empty_supersedes_list(self):
        staging = self._stage_manifest("test-pkg", """\
SUPERSEDES: 
FILE LIST:
file
""")
        decl, _, _ = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertIsNone(decl)


class TestPredecessorValidation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "root"
        self.root.mkdir()
        self.db_path = Path(self.tmp) / "test.db"
        self.db = PackageDB(self.db_path)
        self.installer = PackageInstaller(self.db, root=str(self.root))

    def tearDown(self):
        self.db.close()

    def _install_stub(self, name, version="1.0"):
        staging = Path(self.tmp) / f"staging-{name}"
        staging.mkdir(exist_ok=True)
        (staging / f"{name}.txt").write_text(f"content\n")
        archive_path = staging / f"{name}-{version}.igos.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(staging / f"{name}.txt", arcname=f"{name}.txt")
        self.installer.install(name, archive_path=archive_path)

    def test_missing_supersedee_warns_and_proceeds(self):
        self._install_stub("standalone")
        result = self.installer._validate_predecessors(
            "standalone", ["nonexistent"], None
        )
        self.assertIsNotNone(result)

    def test_already_superseded_warns_and_skips(self):
        self._install_stub("old")
        self.db.mark_superseded("old", "new-meta")

        result = self.installer._validate_predecessors(
            "new-pkg", ["old"], None
        )
        self.assertEqual(result, [])

    def test_queue_order_violation_blocks(self):
        self._install_stub("later-pred")
        self._install_stub("earlier-succ")

        result = self.installer._validate_predecessors(
            "earlier-succ", ["later-pred"],
            queue=["earlier-succ", "later-pred"]
        )
        self.assertIsNone(result)

    def test_install_order_violation_message(self):
        """Ad-hoc verify that the `install()` method rejects predecessor-later-in-queue."""
        self._install_stub("pred", version="0.5")
        staging = Path(self.tmp) / "succ-staging"
        staging.mkdir(exist_ok=True)
        (staging / "succ.txt").write_text("content\n")
        archive = staging / "succ-1.0.igos.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(staging / "succ.txt", arcname="succ.txt")

        ok, msg = self.installer.install(
            "succ", archive_path=archive,
            queue=["succ", "pred"]
        )
        self.assertFalse(ok)
        self.assertIn("install-order", msg)


class TestBuildHashMap(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "root"
        self.root.mkdir()
        self.db_path = Path(self.tmp) / "test.db"
        self.db = PackageDB(self.db_path)
        self.installer = PackageInstaller(self.db, root=str(self.root))

    def tearDown(self):
        self.db.close()

    def test_primary_from_manifest(self):
        staging = Path(self.tmp) / "staging"
        staging.mkdir(exist_ok=True)
        (staging / "file.txt").write_text("hello\n")

        hashes = self.installer._build_hash_map(
            staging, ["file.txt"], {"file.txt": "abc123"}
        )
        self.assertEqual(hashes, {"file.txt": "abc123"})

    def test_fallback_from_staged_tree(self):
        staging = Path(self.tmp) / "staging"
        staging.mkdir(exist_ok=True)
        (staging / "file.txt").write_text("fallback-content\n")

        hashes = self.installer._build_hash_map(
            staging, ["file.txt"], {}
        )
        self.assertIn("file.txt", hashes)
        self.assertEqual(len(hashes["file.txt"]), 64)

    def test_partial_fallback(self):
        staging = Path(self.tmp) / "staging"
        staging.mkdir(exist_ok=True)
        (staging / "a.txt").write_text("a\n")
        (staging / "b.txt").write_text("b\n")

        hashes = self.installer._build_hash_map(
            staging, ["a.txt", "b.txt"], {"a.txt": "manifest-hash"}
        )
        self.assertEqual(hashes["a.txt"], "manifest-hash")
        self.assertEqual(len(hashes["b.txt"]), 64)


if __name__ == "__main__":
    unittest.main()

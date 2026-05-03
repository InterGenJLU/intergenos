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
        # Real manifests carry 64-char sha256 hashes; the parser regex anchors
        # on that length to disambiguate from paths-with-whitespace. Fixture
        # hashes must be 64 hex chars to exercise the production code path.
        sha = "a" * 64
        staging = self._stage_manifest("test-pkg", f"""\
SUPERSEDES: old-pkg
FILE LIST:
fileA sha256:{sha}
fileB
""")
        decl, files, hashes = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertEqual(decl, ["old-pkg"])
        self.assertEqual(files, ["fileA", "fileB"])
        self.assertEqual(hashes, {"fileA": sha})

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
        # Production sha256 hashes are 64 hex chars; the parser regex enforces
        # that length to keep paths-with-whitespace from being mis-split.
        sha = "f" * 64
        staging = self._stage_manifest("test-pkg", f"""\
FILE LIST:
hashed sha256:{sha}
unhashed
""")
        _, _, hashes = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertEqual(hashes, {"hashed": sha})

    def test_empty_supersedes_list(self):
        staging = self._stage_manifest("test-pkg", """\
SUPERSEDES:
FILE LIST:
file
""")
        decl, _, _ = self.installer._read_staged_manifest(staging, "test-pkg")
        self.assertIsNone(decl)  # Phase 4 Bug 2 fix: empty SUPERSEDES → None


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
        # install a predecessor so the method runs correctly
        self._install_stub("pred", version="0.5")
        result = self.installer._validate_predecessors(
            "succ", ["nonexistent"], None
        )
        self.assertIsNotNone(result)  # returns list, not None (None = queue-order violation)

    def test_already_superseded_warns_and_skips(self):
        self._install_stub("old")
        self._install_stub("new")
        self.db.mark_superseded("old", "new")

        result = self.installer._validate_predecessors(
            "another", ["old"], None
        )
        self.assertEqual(result, [])

    def test_queue_order_violation_blocks(self):
        self._install_stub("pred")
        self._install_stub("succ")

        result = self.installer._validate_predecessors(
            "succ", ["pred"],
            queue=["succ", "pred"]
        )
        self.assertIsNone(result)  # None = BLOCKED

    def test_install_order_violation_message(self):
        self._install_stub("pred", files=[("pred.txt", "data\n")])
        self._install_stub("succ", files=[("succ.txt", "data\n")])

        ok, msg = self.installer.install(
            "another-succ", archive_path=None,
            queue=["another-succ", "pred"]
        )
        self.assertFalse(ok)

    def _install_stub(self, name, version="1.0", files=None):
        if files is None:
            files = [(f"{name}.txt", f"content\n")]
        from pkm.database import _sha256
        for path, content in files:
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(content)
        hashes = {}
        import hashlib
        for path, content in files:
            hashes[path] = hashlib.sha256(content.encode()).hexdigest()
        pkg_id = self.db.add_installed(name=name, version=version, install_method="test")
        self.db.add_files(pkg_id, [p for p, _ in files], hashes=hashes)


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

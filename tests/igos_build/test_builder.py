"""Unit tests for igos-build/builder.py.

Covers the security-critical path-traversal validation in tar archive extraction
and URL-to-filename helpers that affect source caching correctness.

Closes the §1 B5 (HIGH) test-coverage gap on builder.py for the highest-stakes
behaviors (path-traversal rejection + source-URL parsing).

Note: full coverage of build_package / build_all / extract_source happy paths
requires substantial subprocess + filesystem mocking. This module focuses on
the pure-function and security-critical logic that's amenable to fast unit
tests. End-to-end build behavior is exercised by the build VM (per the build
runbook), not by this unit test module.
"""

import importlib
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

# Import the LIVE helpers from the worktree this test file lives in — never
# an inline reproduction (review finding M3: the reproduced copy silently
# drifted, still carrying the startswith containment check after the live
# code moved to is_relative_to) and never a hardcoded absolute root.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
_builder_mod = importlib.import_module("igos-build.builder")
_url_basename = _builder_mod._url_basename
_validate_tar_members = _builder_mod._validate_tar_members


class _CapturingLogger:
    """Minimal logger stand-in that captures error messages for assertions."""

    def __init__(self):
        self.errors = []

    def error(self, msg):
        self.errors.append(msg)


class TestUrlBasename(unittest.TestCase):
    """Coverage of _url_basename — URL-to-filename extraction."""

    def test_simple_filename(self):
        self.assertEqual(
            _url_basename("https://example.com/pkg-1.0.tar.gz"),
            "pkg-1.0.tar.gz"
        )

    def test_strips_query_string(self):
        """CDN/token-bearing URLs must not contaminate the cached filename."""
        self.assertEqual(
            _url_basename("https://foo.com/pkg-1.0.tar.gz?token=xyz"),
            "pkg-1.0.tar.gz"
        )

    def test_strips_fragment(self):
        self.assertEqual(
            _url_basename("https://foo.com/pkg-1.0.tar.gz#sha256=abc"),
            "pkg-1.0.tar.gz"
        )

    def test_gitlab_archive_path_extracts_correctly(self):
        """GitLab uses /-/archive/v<ver>/ which must still extract correctly."""
        url = "https://gitlab.com/group/project/-/archive/v1.0/project-v1.0.tar.gz"
        self.assertEqual(_url_basename(url), "project-v1.0.tar.gz")

    def test_github_release_url(self):
        url = "https://github.com/foo/bar/releases/download/v1.0/bar-1.0.tar.gz"
        self.assertEqual(_url_basename(url), "bar-1.0.tar.gz")

    def test_url_without_path_returns_empty(self):
        # Edge case: no path component; should not crash
        self.assertEqual(_url_basename("https://example.com"), "")


class TestValidateTarMembers(unittest.TestCase):
    """Coverage of _validate_tar_members — path-traversal rejection.

    Critical security boundary: source archives are downloaded from upstream
    and extracted into the build tree. A malicious upstream tarball with
    members like '../etc/passwd' would write outside the extraction root
    without this validation.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="test-builder-"))
        self.dest_dir = self.tmpdir / "extract"
        self.dest_dir.mkdir()
        self.logger = _CapturingLogger()

    def tearDown(self):
        # Best-effort cleanup; tests don't write under self.dest_dir.
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_tar(self, member_names):
        """Build a tarball with the given member names + return its path.

        Each member is a regular file with empty content."""
        tar_path = self.tmpdir / "test.tar"
        with tarfile.open(str(tar_path), "w") as tf:
            for name in member_names:
                info = tarfile.TarInfo(name=name)
                info.size = 0
                tf.addfile(info, io.BytesIO(b""))
        return tar_path

    def test_safe_paths_pass(self):
        tar_path = self._make_tar([
            "pkg-1.0/README",
            "pkg-1.0/src/main.c",
            "pkg-1.0/Makefile",
        ])
        self.assertTrue(_validate_tar_members(tar_path, self.dest_dir, self.logger))
        self.assertEqual(self.logger.errors, [])

    def test_dot_dot_path_rejected(self):
        """Member like '../etc/passwd' must be rejected."""
        tar_path = self._make_tar([
            "pkg-1.0/safe-file",
            "../etc/passwd",
        ])
        self.assertFalse(_validate_tar_members(tar_path, self.dest_dir, self.logger))
        self.assertEqual(len(self.logger.errors), 1)
        self.assertIn("SECURITY", self.logger.errors[0])
        self.assertIn("../etc/passwd", self.logger.errors[0])

    def test_absolute_path_rejected(self):
        """Member with an absolute path must be rejected — fail closed.

        Python's tarfile stores the member name verbatim, so '/etc/shadow'
        joins as an absolute path, resolves outside dest_dir, and the
        containment check must reject the archive. (Review finding M3: this
        case previously accepted either outcome.)"""
        tar_path = self.tmpdir / "abs.tar"
        with tarfile.open(str(tar_path), "w") as tf:
            info = tarfile.TarInfo(name="/etc/shadow")
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))
        self.assertFalse(
            _validate_tar_members(tar_path, self.dest_dir, self.logger))
        self.assertEqual(len(self.logger.errors), 1)
        self.assertIn("SECURITY", self.logger.errors[0])

    def test_sibling_prefix_collision_rejected(self):
        """A member resolving to a SIBLING of dest sharing its name prefix
        must be rejected (the startswith-era escape class: '/x/y-evil'
        passes a str prefix check against '/x/y')."""
        sibling = f"../{self.dest_dir.name}-evil/payload"
        tar_path = self._make_tar([sibling])
        self.assertFalse(
            _validate_tar_members(tar_path, self.dest_dir, self.logger))
        self.assertEqual(len(self.logger.errors), 1)
        self.assertIn("SECURITY", self.logger.errors[0])

    def test_nested_dot_dot_traversal_rejected(self):
        """Member like 'pkg/../../escape' must be rejected (nested traversal)."""
        tar_path = self._make_tar([
            "pkg/normal",
            "pkg/../../escape",
        ])
        self.assertFalse(_validate_tar_members(tar_path, self.dest_dir, self.logger))
        self.assertGreaterEqual(len(self.logger.errors), 1)
        self.assertIn("SECURITY", self.logger.errors[0])

    def test_corrupt_tar_returns_false(self):
        """A corrupt or non-tar file should fail safely (return False, log error)."""
        bad_path = self.tmpdir / "not-a-tar.txt"
        bad_path.write_bytes(b"not a tar archive at all")
        result = _validate_tar_members(bad_path, self.dest_dir, self.logger)
        self.assertFalse(result)
        self.assertEqual(len(self.logger.errors), 1)
        self.assertIn("Failed to inspect tar", self.logger.errors[0])

    def test_empty_tar_passes(self):
        """An empty (but valid) tar should pass — no members to reject."""
        tar_path = self._make_tar([])
        self.assertTrue(_validate_tar_members(tar_path, self.dest_dir, self.logger))


if __name__ == "__main__":
    unittest.main()

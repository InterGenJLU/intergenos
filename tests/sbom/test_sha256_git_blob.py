"""Unit tests for the sha256_git_blob() helper in scripts/shim-sbom-gen.py.

Covers the F17 invariant: working-tree CRLF transformations or arbitrary
uncommitted edits must NOT affect emitted SHAs. sha256_git_blob() must
always return the SHA of the content committed at HEAD. Also covers the
not-in-HEAD error path (helper surfaces CalledProcessError so callers
can decide failure behavior).

Each test builds a minimal git-init-ed temp repo, commits known-content
files, then exercises sha256_git_blob() against various working-tree
states. Test setup explicitly disables core.autocrlf so working-tree
write paths are deterministic across host platforms; the function under
test is host-independent by design (reads via ``git show HEAD:``).
"""

import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "shim-sbom-gen.py"

# Hyphen in script name blocks normal import; load via importlib spec
# (same pattern used in tests/preflight/).
_spec = importlib.util.spec_from_file_location("shim_sbom_gen", SCRIPT_PATH)
sbom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sbom)


def _git(repo: Path, *args: str) -> None:
    """Run a git subcommand inside `repo`; raise on non-zero exit."""
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


def _git_init(repo: Path) -> None:
    """Initialize a minimal git repo with deterministic identity + config."""
    _git(repo, "init", "--initial-branch=main", "--quiet")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    # Disable autocrlf so working-tree writes stay byte-stable across hosts;
    # the function under test is autocrlf-independent by design, but a
    # deterministic working-tree write path keeps the test setup honest.
    _git(repo, "config", "core.autocrlf", "false")


class TestSha256GitBlob(unittest.TestCase):
    """sha256_git_blob() — F17 invariant + error path."""

    def test_returns_committed_sha_canonical_lf(self):
        """Canonical LF content → SHA of committed bytes."""
        with tempfile.TemporaryDirectory() as tmp_:
            repo = Path(tmp_)
            _git_init(repo)
            content = b"hello\nworld\n"
            (repo / "file.txt").write_bytes(content)
            _git(repo, "add", "file.txt")
            _git(repo, "commit", "-m", "add file", "--quiet")

            expected = hashlib.sha256(content).hexdigest()
            actual = sbom.sha256_git_blob(repo, "file.txt")
            self.assertEqual(actual, expected)

    def test_working_tree_crlf_corruption_does_not_affect_sha(self):
        """F17 invariant: CRLF in working tree, LF in HEAD → SHA tracks HEAD."""
        with tempfile.TemporaryDirectory() as tmp_:
            repo = Path(tmp_)
            _git_init(repo)
            canonical_lf = b"line1\nline2\nline3\n"
            (repo / "cert.pem").write_bytes(canonical_lf)
            _git(repo, "add", "cert.pem")
            _git(repo, "commit", "-m", "add cert", "--quiet")

            expected_committed_sha = hashlib.sha256(canonical_lf).hexdigest()

            # Corrupt the working tree with CRLF (the exact F17 failure mode).
            crlf_corrupted = canonical_lf.replace(b"\n", b"\r\n")
            (repo / "cert.pem").write_bytes(crlf_corrupted)

            # Sanity check the setup: disk SHA must differ from committed SHA.
            disk_sha = hashlib.sha256(
                (repo / "cert.pem").read_bytes()
            ).hexdigest()
            self.assertNotEqual(disk_sha, expected_committed_sha)

            # sha256_git_blob() must return the committed SHA, not disk SHA.
            actual = sbom.sha256_git_blob(repo, "cert.pem")
            self.assertEqual(actual, expected_committed_sha)
            self.assertNotEqual(actual, disk_sha)

    def test_working_tree_unrelated_edit_does_not_affect_sha(self):
        """Any uncommitted working-tree edit → SHA stays on HEAD content."""
        with tempfile.TemporaryDirectory() as tmp_:
            repo = Path(tmp_)
            _git_init(repo)
            committed = b"original committed content\n"
            (repo / "data.bin").write_bytes(committed)
            _git(repo, "add", "data.bin")
            _git(repo, "commit", "-m", "init", "--quiet")

            expected = hashlib.sha256(committed).hexdigest()

            # Edit working tree (completely different content).
            (repo / "data.bin").write_bytes(b"completely different\n")

            actual = sbom.sha256_git_blob(repo, "data.bin")
            self.assertEqual(actual, expected)

    def test_path_not_in_head_raises_called_process_error(self):
        """Missing file in HEAD → CalledProcessError (caller-handled)."""
        with tempfile.TemporaryDirectory() as tmp_:
            repo = Path(tmp_)
            _git_init(repo)
            # Commit one file so HEAD exists.
            (repo / "real.txt").write_bytes(b"present\n")
            _git(repo, "add", "real.txt")
            _git(repo, "commit", "-m", "init", "--quiet")

            with self.assertRaises(subprocess.CalledProcessError):
                sbom.sha256_git_blob(repo, "does-not-exist.txt")

    def test_staged_uncommitted_file_raises(self):
        """Staged-but-not-committed file → CalledProcessError (HEAD is truth)."""
        with tempfile.TemporaryDirectory() as tmp_:
            repo = Path(tmp_)
            _git_init(repo)
            # Anchor commit so HEAD exists.
            (repo / "anchor.txt").write_bytes(b"anchor\n")
            _git(repo, "add", "anchor.txt")
            _git(repo, "commit", "-m", "anchor", "--quiet")

            # Stage but DO NOT commit a new file.
            (repo / "uncommitted.txt").write_bytes(b"staged not committed\n")
            _git(repo, "add", "uncommitted.txt")

            with self.assertRaises(subprocess.CalledProcessError):
                sbom.sha256_git_blob(repo, "uncommitted.txt")


if __name__ == "__main__":
    unittest.main()

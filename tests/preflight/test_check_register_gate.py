"""The push-time writing-register gate refuses and passes what it should.

The two-tier layer (decided 2026-08-16): a narrow tier that refuses
machine-assistant self-narration phrasing in newly added prose, and a broad
advisory tier that prints without refusing. These tests drive the REAL
scanner via subprocess against throwaway git repositories, both directions:
the violating shape refused, the legitimate neighbour passed.

Probe strings are ASSEMBLED at runtime (the established scanner-test
discipline) so this file's own added lines never carry an intact tier
pattern — test code is not a prose zone, but the discipline costs nothing
and keeps grep-level sweeps quiet.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCANNER = REPO / "scripts" / "check-register.py"

BLOCK_PROBE = "we've " + "success" + "fully migrated"
WARN_PROBE = "a " + "seam" + "less upgrade path"
CLEAN_LINE = "the loader reads the manifest before the first mount"


def _repo_with_push_range(tmp_path: Path, relpath: str, content: str,
                          exclusions: str = "# none\n") -> tuple[Path, str]:
    """A git repo with one baseline commit and one commit adding `content`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True,
                                    text=True, check=True)
    run("init", "-q")
    run("config", "user.name", "t")
    run("config", "user.email", "t@t.invalid")
    (repo / "config").mkdir()
    (repo / "config/register-gate-exclusions.txt").write_text(exclusions)
    (repo / "base.md").write_text("baseline\n")
    run("add", "-A")
    run("commit", "-q", "-m", "chore: baseline")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    run("add", "-A")
    run("commit", "-q", "-m", "chore: probe")
    return repo, f"{base}..HEAD"


def _scan(repo: Path, rng: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(SCANNER), "--range", rng, "--repo", str(repo)],
        capture_output=True, text=True)


class TestBlockTier:
    def test_self_narration_in_doc_refused(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/notes.md",
                                          BLOCK_PROBE + "\n")
        r = _scan(repo, rng)
        assert r.returncode == 1
        assert "BLOCK[ai-self-narration]" in r.stdout

    def test_self_narration_in_code_comment_refused(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "scripts/x.sh",
                                          "# " + BLOCK_PROBE + "\n")
        r = _scan(repo, rng)
        assert r.returncode == 1

    def test_same_phrase_in_code_string_passes(self, tmp_path):
        # A string literal is not a prose zone; the gate is self-safe by
        # construction and this pins that property.
        repo, rng = _repo_with_push_range(tmp_path, "scripts/x.py",
                                          f'MSG = "{BLOCK_PROBE}"\n')
        r = _scan(repo, rng)
        assert r.returncode == 0

    def test_block_phrase_in_commit_message_refused(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/clean.md",
                                          CLEAN_LINE + "\n")
        subprocess.run(["git", "commit", "-q", "--allow-empty",
                        "-m", "chore: note\n\n" + BLOCK_PROBE],
                       cwd=repo, check=True)
        r = _scan(repo, rng.split("..")[0] + "..HEAD")
        assert r.returncode == 1
        assert "commit" in r.stdout


class TestWarnTier:
    def test_warn_class_prints_but_passes(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/notes.md",
                                          WARN_PROBE + "\n")
        r = _scan(repo, rng)
        assert r.returncode == 0
        assert "WARN[hype-adjective]" in r.stdout

    def test_bare_warning_sign_house_mark_not_flagged(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/notes.md",
                                          "⚠ mount point missing\n")
        r = _scan(repo, rng)
        assert r.returncode == 0
        assert "WARN[emoji]" not in r.stdout

    def test_emoji_presentation_flagged_advisory(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/notes.md",
                                          "done ✅\n")
        r = _scan(repo, rng)
        assert r.returncode == 0
        assert "WARN[emoji]" in r.stdout


class TestExclusionsAndFailClosed:
    def test_excluded_prefix_skipped(self, tmp_path):
        repo, rng = _repo_with_push_range(
            tmp_path, "docs/research/eval.md", BLOCK_PROBE + "\n",
            exclusions="docs/research\n")
        r = _scan(repo, rng)
        assert r.returncode == 0
        assert "BLOCK" not in r.stdout

    def test_missing_exclusion_file_is_loud_failure(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/notes.md",
                                          CLEAN_LINE + "\n")
        (repo / "config/register-gate-exclusions.txt").unlink()
        r = _scan(repo, rng)
        assert r.returncode == 2
        assert "OPERATIONAL FAILURE" in r.stdout

    def test_clean_range_passes_quietly(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/notes.md",
                                          CLEAN_LINE + "\n")
        r = _scan(repo, rng)
        assert r.returncode == 0
        assert "PASS" in r.stdout

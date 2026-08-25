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


class TestWrappedAcrossALine:
    """A tier pattern split by a line wrap must be seen (decided 2026-08-25).

    The gate matched each added line on its own, so any pattern spelled as two
    or more words passed whenever the wrap fell between its words. The added
    prose-zone lines of a file are now scanned in runs of consecutive lines
    joined by one space, and each commit message as one run.

    Probe strings stay ASSEMBLED at run time, as everywhere in this file.
    """

    # The block-tier probe with the wrap placed between its two words.
    WRAP_HEAD = "we've"
    WRAP_TAIL = "success" + "fully migrated the loader"

    def test_block_phrase_wrapped_across_two_doc_lines_refused(self, tmp_path):
        repo, rng = _repo_with_push_range(
            tmp_path, "docs/notes.md",
            f"the mount order changed and {self.WRAP_HEAD}\n{self.WRAP_TAIL}\n")
        r = _scan(repo, rng)
        assert r.returncode == 1, r.stdout
        assert "BLOCK[ai-self-narration]" in r.stdout
        assert "docs/notes.md:1:" in r.stdout, (
            f"the report must name the line the match STARTS on:\n{r.stdout}")

    def test_warn_phrase_wrapped_across_two_doc_lines_prints_and_passes(self, tmp_path):
        # A WARN-tier entry spelled as three words, wrapped between the second
        # and the third. A one-word entry broken mid-word is a different case
        # and must NOT match — the join adds a space, so the halves are two
        # words; that direction is pinned in the language gate's own tests.
        head, tail = "keep in", "mind" + " that the mount runs first"
        repo, rng = _repo_with_push_range(
            tmp_path, "docs/notes.md", f"before the upgrade {head}\n{tail}\n")
        r = _scan(repo, rng)
        assert r.returncode == 0, r.stdout
        assert "WARN[filler-phrase]" in r.stdout
        assert "docs/notes.md:1:" in r.stdout

    def test_block_phrase_wrapped_in_a_code_comment_is_not_joined(self, tmp_path):
        repo, rng = _repo_with_push_range(
            tmp_path, "scripts/x.sh",
            f"# the mount order changed and {self.WRAP_HEAD}\n"
            f"# {self.WRAP_TAIL}\n")
        r = _scan(repo, rng)
        # The comment marker of the second line sits between the two words, so
        # this wrap is NOT joined into a match. Pinned as measured behaviour:
        # the run is formed from the author's line text, markers included.
        assert r.returncode == 0, r.stdout

    def test_a_code_line_between_two_comment_lines_breaks_the_run(self, tmp_path):
        # Non-prose lines are dropped BEFORE the run is formed, so the drop
        # shows up as a break rather than joining two comments across code.
        repo, rng = _repo_with_push_range(
            tmp_path, "scripts/x.py",
            f"# the loader changed and {self.WRAP_HEAD}\n"
            "VALUE = 1\n"
            f"# {self.WRAP_TAIL}\n")
        r = _scan(repo, rng)
        assert r.returncode == 0, r.stdout
        assert "BLOCK" not in r.stdout

    def test_block_phrase_wrapped_in_a_commit_message_refused(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/clean.md",
                                          CLEAN_LINE + "\n")
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m",
             f"chore: note\n\nthe mount order changed and {self.WRAP_HEAD}\n"
             f"{self.WRAP_TAIL}\n"],
            cwd=repo, check=True)
        r = _scan(repo, rng.split("..")[0] + "..HEAD")
        assert r.returncode == 1, r.stdout
        assert "commit" in r.stdout

    def test_single_line_hit_still_refused_after_the_change(self, tmp_path):
        repo, rng = _repo_with_push_range(tmp_path, "docs/notes.md",
                                          BLOCK_PROBE + "\n")
        r = _scan(repo, rng)
        assert r.returncode == 1, r.stdout
        assert "docs/notes.md:1:" in r.stdout

    def test_one_report_per_line_and_class(self, tmp_path):
        # A line hitting one class twice was reported once before the run join
        # and is reported once after it.
        probe = BLOCK_PROBE + " and " + BLOCK_PROBE
        repo, rng = _repo_with_push_range(tmp_path, "docs/notes.md", probe + "\n")
        r = _scan(repo, rng)
        assert r.returncode == 1, r.stdout
        assert r.stdout.count("BLOCK[ai-self-narration]") == 1, r.stdout

    def test_an_indented_continuation_line_is_still_a_hit(self, tmp_path):
        # The tier patterns are spelled with one literal space, so the wrap's
        # own whitespace has to be normalized into the join for an indented
        # continuation line to read as the next word.
        repo, rng = _repo_with_push_range(
            tmp_path, "docs/notes.md",
            f"the mount order changed and {self.WRAP_HEAD}\n"
            f"      {self.WRAP_TAIL}\n")
        r = _scan(repo, rng)
        assert r.returncode == 1, r.stdout
        assert "BLOCK[ai-self-narration]" in r.stdout

    def test_a_blank_line_breaks_the_run(self, tmp_path):
        repo, rng = _repo_with_push_range(
            tmp_path, "docs/notes.md",
            f"the mount order changed and {self.WRAP_HEAD}\n\n{self.WRAP_TAIL}\n")
        r = _scan(repo, rng)
        assert r.returncode == 0, r.stdout
        assert "BLOCK" not in r.stdout

    def test_a_commit_subject_does_not_join_its_body(self, tmp_path):
        # The blank line between a subject and its body breaks the run, so a
        # phrase spanning the two is not matched.
        repo, rng = _repo_with_push_range(tmp_path, "docs/clean.md",
                                          CLEAN_LINE + "\n")
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m",
             f"chore: the mount order changed and {self.WRAP_HEAD}\n\n"
             f"{self.WRAP_TAIL}\n"],
            cwd=repo, check=True)
        r = _scan(repo, rng.split("..")[0] + "..HEAD")
        assert r.returncode == 0, r.stdout

    def test_two_innocent_words_wrapped_pass(self, tmp_path):
        repo, rng = _repo_with_push_range(
            tmp_path, "docs/notes.md",
            "the loader reads the boot\nmanifest before the first mount\n")
        r = _scan(repo, rng)
        assert r.returncode == 0, r.stdout
        assert "PASS" in r.stdout

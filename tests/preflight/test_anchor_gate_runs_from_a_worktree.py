"""The anchor gate must not mistake a git worktree for "not a git repository".

WHY THIS EXISTS (measured 2026-08-25, through the real pre-push hook)
---------------------------------------------------------------------
While proving the anchor gate's remote-URL narrowing, the gate was driven from a
git worktree — the ordinary shape for authoring here — and aborted with:

    ERROR: PUBLIC_REPO (<the worktree path>) not a git repo
    [pre-push] BLOCK: TRACKER anchor advancement failed.

The directory plainly is a git repository. The check asked the wrong question:
it tested ``[ -d "$REPO/.git" ]``, and in a worktree ``.git`` is a FILE holding a
``gitdir:`` pointer, not a directory. The same predicate also sat in the
private-repository discovery chain, where a worktree would be skipped silently
and the script would fall through to "private repo not found".

The consequence is not theoretical. This project's own layout keeps the
development checkout in a worktree, so a promotion pushed from it was refused by
a message that misstated the cause — and the refusal arrived after the push had
already been accepted by every other gate.

WHY THIS IS A WIDENING, AND WHY IT WAS DECIDED BEFORE IT WAS WRITTEN
The gate performs an outward write into another repository. The previous
behaviour fails CLOSED: the push is blocked and nothing is written. Correcting it
makes the gate RUN in situations where it previously refused, which enlarges
where an outward write can happen. Decided 2026-08-25: the gate is meant to run
from the development checkout, that checkout is a worktree, and a guard that
blocks its own intended use is not protecting anything.

WHAT IS ASSERTED
  * a worktree is accepted as a repository, for both the public and the private
    side, and by the discovery chain as well as the assertions;
  * a path that is genuinely not a repository is still refused, and the refusal
    names what was actually tried;
  * no ``-d .../.git`` shape test survives anywhere in the script, so the defect
    cannot return by being reintroduced somewhere the tests do not look.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def _repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True,
                         cwd=Path(__file__).resolve().parent)
    return Path(out.stdout.strip())


REPO = _repo_root()
TRACKER_SH = REPO / "scripts" / "anchor-tracker.sh"
COAUTHOR_ENV = "INTERGENOS_COMMIT_COAUTHOR"


def _private_repo_dirname() -> str:
    """The directory the script looks for, read from the script itself.

    Duplicating the name here would let a rename pass silently: the test would
    build one directory while the script searched for another, and the discovery
    test would fail for an unrelated reason.
    """
    for line in TRACKER_SH.read_text().splitlines():
        s = line.strip()
        if s.startswith("PRIVATE_REPO_DIRNAME="):
            name = s.split("=", 1)[1].strip().strip('"')
            if name:
                return name
    raise AssertionError(
        "could not read the private-repository directory name from %s — the "
        "assignment this test reads it from has changed shape" % TRACKER_SH)


PRIVATE_DIRNAME = _private_repo_dirname()
COAUTHOR_VALUE = "Worktree Gate Test <noreply@example.invalid>"


def _git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=True, env=env, timeout=120)


class _WorktreeFixture(unittest.TestCase):
    """A real repository, a real worktree of it, and a real ledger file.

    Built with git rather than by writing a fake ``.git`` file, because the whole
    point is the shape git actually produces.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wtgate-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()

        self.env = dict(os.environ)
        self.env["HOME"] = str(self.home)
        self.env["GIT_CONFIG_GLOBAL"] = str(self.home / ".gitconfig")
        self.env["GIT_CONFIG_SYSTEM"] = os.devnull
        for k, v in (("GIT_AUTHOR_NAME", "Gate Test"),
                     ("GIT_AUTHOR_EMAIL", "gate@example.invalid"),
                     ("GIT_COMMITTER_NAME", "Gate Test"),
                     ("GIT_COMMITTER_EMAIL", "gate@example.invalid")):
            self.env[k] = v
        self.env.pop("INTERGENOS_PUBLIC_REPO", None)
        self.env.pop("INTERGENOS_PRIVATE_REPO", None)
        self.env[COAUTHOR_ENV] = COAUTHOR_VALUE

        # Public side: a normal clone, plus a worktree of it.
        self.public_main = self.tmp / "public"
        self.public_main.mkdir()
        _git(self.public_main, "init", "-q", "-b", "master", env=self.env)
        (self.public_main / "README.md").write_text("scratch\n")
        _git(self.public_main, "add", "README.md", env=self.env)
        _git(self.public_main, "commit", "-qm", "initial", env=self.env)
        self.public_wt = self.tmp / "public-worktree"
        _git(self.public_main, "worktree", "add", "--detach",
             str(self.public_wt), "HEAD", env=self.env)
        self.target_sha = _git(self.public_wt, "rev-parse", "HEAD",
                               env=self.env).stdout.strip()

        # Private side: a normal clone with a ledger, plus a worktree of it.
        self.private_main = self.tmp / "private"
        self.private_main.mkdir()
        _git(self.private_main, "init", "-q", "-b", "master", env=self.env)
        self.ledger_name = self._ledger_filename()
        (self.private_main / self.ledger_name).write_text(
            "# scratch\n\n<!-- ANCHOR: public-master HEAD deadbeef -->\n")
        _git(self.private_main, "add", self.ledger_name, env=self.env)
        _git(self.private_main, "commit", "-qm", "initial", env=self.env)
        self.private_wt = self.tmp / "private-worktree"
        _git(self.private_main, "worktree", "add", str(self.private_wt),
             "-b", "anchor-side", "HEAD", env=self.env)

    @staticmethod
    def _ledger_filename() -> str:
        """Read the ledger's name from the script, so a rename cannot desync."""
        for line in TRACKER_SH.read_text().splitlines():
            s = line.strip()
            if s.startswith("TRACKER=") and "PRIVATE_REPO" in s:
                name = s.split("=", 1)[1].strip().strip('"').rsplit("/", 1)[-1]
                if name:
                    return name
        raise AssertionError("could not read the ledger filename from the script")

    def run_tracker(self, *args, public=None, private=None, env_extra=None):
        env = dict(self.env)
        if public is not None:
            env["INTERGENOS_PUBLIC_REPO"] = str(public)
        if private is not None:
            env["INTERGENOS_PRIVATE_REPO"] = str(private)
        if env_extra:
            env.update(env_extra)
        return subprocess.run([str(TRACKER_SH), *args], capture_output=True,
                              text=True, env=env, cwd=str(self.tmp), timeout=120)


class TheGateAcceptsAWorktree(_WorktreeFixture):

    def test_the_fixture_really_is_the_shape_that_broke_it(self) -> None:
        """Prove the premise before relying on it.

        If the fixture's .git were a directory, every test below would pass
        without exercising anything.
        """
        for wt in (self.public_wt, self.private_wt):
            with self.subTest(worktree=str(wt)):
                dot_git = wt / ".git"
                self.assertTrue(dot_git.exists(), f"{dot_git} missing")
                self.assertTrue(
                    dot_git.is_file(),
                    f"{dot_git} is a directory — this fixture is not a worktree "
                    f"and would not reproduce the defect")
                self.assertTrue(
                    dot_git.read_text().startswith("gitdir:"),
                    "the worktree's .git file does not carry a gitdir pointer")

    def test_a_public_worktree_is_accepted(self) -> None:
        r = self.run_tracker("--dry-run", self.target_sha,
                             public=self.public_wt, private=self.private_main)
        self.assertEqual(
            r.returncode, 0,
            "the script refused a public repository that is a git worktree\n"
            "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertNotIn("not a git repo", r.stdout + r.stderr)

    def test_a_private_worktree_is_accepted(self) -> None:
        r = self.run_tracker("--dry-run", self.target_sha,
                             public=self.public_main, private=self.private_wt)
        self.assertEqual(
            r.returncode, 0,
            "the script refused a private repository that is a git worktree\n"
            "stdout=%r stderr=%r" % (r.stdout, r.stderr))

    def test_both_sides_worktrees_at_once(self) -> None:
        """The shape this project actually authors in."""
        r = self.run_tracker("--dry-run", self.target_sha,
                             public=self.public_wt, private=self.private_wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_discovery_finds_a_private_worktree_under_home(self) -> None:
        """The discovery chain carried the same predicate as the assertions.

        With INTERGENOS_PRIVATE_REPO unset, the script falls back to a path under
        HOME. A worktree there was skipped silently, and the script reported the
        private repository as not found — a different message for the same bug.
        """
        home_private = self.home / PRIVATE_DIRNAME
        _git(self.private_main, "worktree", "add", str(home_private),
             "-b", "home-side", "HEAD", env=self.env)
        env = dict(self.env)
        env["INTERGENOS_PUBLIC_REPO"] = str(self.public_wt)
        env.pop("INTERGENOS_PRIVATE_REPO", None)
        r = subprocess.run([str(TRACKER_SH), "--dry-run", self.target_sha],
                           capture_output=True, text=True, env=env,
                           cwd=str(self.tmp), timeout=120)
        self.assertEqual(
            r.returncode, 0,
            "discovery did not find a private repository that is a worktree\n"
            "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn(str(home_private), r.stdout + r.stderr,
                      "the discovered path is not the worktree under HOME")


class SomethingThatIsNotARepositoryIsStillRefused(_WorktreeFixture):
    """Widening the accepted shape must not accept everything."""

    def test_a_plain_directory_is_refused(self) -> None:
        plain = self.tmp / "not-a-repo"
        plain.mkdir()
        r = self.run_tracker("--dry-run", self.target_sha,
                             public=plain, private=self.private_main)
        self.assertNotEqual(
            r.returncode, 0,
            "a directory that is not a git repository was accepted\n"
            "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn(str(plain), r.stdout + r.stderr,
                      "the refusal does not name the path it rejected")

    def test_the_refusal_names_what_was_actually_tried(self) -> None:
        """The old message asserted a conclusion; it must state the test.

        "not a git repo" was flatly wrong about a worktree, and a person reading
        it had no way to tell what the script had checked. The message now names
        the command whose failure produced it.
        """
        plain = self.tmp / "not-a-repo-2"
        plain.mkdir()
        r = self.run_tracker("--dry-run", self.target_sha,
                             public=plain, private=self.private_main)
        self.assertIn("rev-parse", (r.stdout + r.stderr),
                      "the refusal does not say how repository shape was tested")

    def test_a_missing_path_is_refused(self) -> None:
        r = self.run_tracker("--dry-run", self.target_sha,
                             public=self.tmp / "absent", private=self.private_main)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


class TheOldPredicateIsGoneFromTheScript(unittest.TestCase):
    """Structural, so the defect cannot return where the tests do not look."""

    @staticmethod
    def _offending_lines(text: str) -> list[str]:
        """Executable lines carrying a `[ -d .../.git ]` test.

        COMMENTS ARE EXCLUDED, and that is not a convenience. The script's own
        header explains why the directory test is wrong and quotes it to do so;
        a scanner that reads prose as code fails on the documentation of the fix
        it is checking for. Measured while writing this test.

        The exclusion creates its own risk — a scanner that ignores too much
        finds nothing — so test_the_scanner_can_actually_catch_one below feeds it
        a real offender and requires a hit.
        """
        out = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            code = stripped.split(" #", 1)[0]
            if re.search(r'\[\s*-d\s+"[^"]*/\.git"', code):
                out.append(stripped)
        return out

    def test_no_dot_git_directory_test_survives(self) -> None:
        offenders = self._offending_lines(TRACKER_SH.read_text())
        self.assertEqual(
            offenders, [],
            "a [ -d .../.git ] shape test is still present in executable code, "
            "which reads a git worktree as not a repository:\n  "
            + "\n  ".join(offenders))

    def test_the_scanner_can_actually_catch_one(self) -> None:
        """A true-positive control for the scanner directly above.

        Without this, "no offenders found" could mean the pattern never matches
        anything, and the structural guard would be decoration.
        """
        planted = (
            '# NOT `[ -d "$path/.git" ]` — this comment must be ignored\n'
            'if [ -d "$PUBLIC_REPO/.git" ]; then\n'
            '    echo yes\n'
            'fi\n'
        )
        hits = self._offending_lines(planted)
        self.assertEqual(
            len(hits), 1,
            "the scanner did not find exactly the one planted offender "
            "(it must ignore the comment and catch the code): %r" % hits)
        self.assertTrue(hits[0].startswith("if [ -d"))

    def test_shape_is_tested_with_rev_parse(self) -> None:
        """Asserted on a boolean, not with assertIn on the file's whole text.

        assertIn prints its container on failure, so asserting against the
        script body dumps the entire script into the capture and buries every
        other line of the run. Measured while writing this file.
        """
        body = TRACKER_SH.read_text()
        self.assertTrue(
            "rev-parse --git-dir" in body,
            "the script does not test repository shape with "
            "`git rev-parse --git-dir` anywhere in its %d lines"
            % len(body.splitlines()))


if __name__ == "__main__":
    unittest.main()

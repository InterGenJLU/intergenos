"""The TRACKER-anchor gate must write outward only for a real promotion.

WHY THIS TEST EXISTS (measured 2026-08-24, in this repository)
--------------------------------------------------------------
A proof harness for an unrelated change created a throwaway clone of this
repository, gave it a LOCAL BARE REMOTE, and pushed ``master`` at it to
exercise a different gate.  The push was entirely local and entirely
disposable.  The pre-push chain's anchor gate does not agree: it fires on the
ref name alone (``refs/heads/master``), resolves the private repository by an
absolute path that has nothing to do with the remote being pushed to, and
COMMITTED AND PUSHED a TRACKER anchor line into the real private repository —
recording a promotion that never happened.  A correcting commit was needed to
undo it.

Two mechanism defects were in that one event:

1.  The gate could not tell a real promotion from a test push, because the
    hook never read the remote it was pushing to.  git hands a pre-push hook
    the remote NAME as ``$1`` and the remote URL as ``$2``; the hook read
    neither.  The remote NAME is not the answer either — a throwaway clone is
    free to call its local bare remote "origin".  Only the URL identifies the
    real publication target.

2.  The anchor commit carried a hard-coded co-author trailer naming a model
    that may not be the one running.  A trailer is a disclosure of who did the
    work; a hard-coded one asserts something the script cannot know.

Both are outward, hard-to-reverse writes, so both fail CLOSED here: an
unrecognised remote does not anchor, and an unstated author does not commit.

WHAT IS ASSERTED
----------------
* the URL matcher accepts every spelling of the real public origin and rejects
  local paths, ``file://`` URLs, lookalike hosts, the private repository, and
  the empty string;
* the pre-push hook captures its own ``$2`` and routes the anchor decision
  through the matcher;
* ``anchor-tracker.sh`` refuses, loudly and by a named reason, when the
  co-author environment variable is unset or malformed, and uses it when set;
* ``anchor-tracker.sh --dry-run`` changes nothing in either repository.

Every check below runs the REAL scripts as subprocesses.  Nothing here is a
re-implementation of the logic under test.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
        cwd=Path(__file__).resolve().parent,
    )
    return Path(out.stdout.strip())


REPO = _repo_root()
MATCHER = REPO / "scripts" / "anchor-remote-is-public-origin.sh"
TRACKER_SH = REPO / "scripts" / "anchor-tracker.sh"
PRE_PUSH = REPO / ".githooks" / "pre-push"

# The environment variable the anchor commit's co-author trailer is read from.
# Named here so a rename cannot pass silently: this constant and the script's
# own header must agree.
COAUTHOR_ENV = "INTERGENOS_COMMIT_COAUTHOR"

# The owner/repository half of the one publication target. Used to build the
# accepted URLs and, by extension, the near-misses that must be refused.
_ORIGIN_PATH = "InterGenJLU/intergenos"


def _ledger_filename() -> str:
    """The ledger file anchor-tracker.sh edits, read from the script itself.

    Hard-coding the name here would duplicate a constant across a boundary and
    let a rename pass silently: the test would build one file while the script
    looked for another, and every check below would fail for an unrelated
    reason.  Reading it from the script keeps the two in step.
    """
    for line in TRACKER_SH.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("TRACKER=") and "PRIVATE_REPO" in stripped:
            value = stripped.split("=", 1)[1].strip().strip('"')
            name = value.rsplit("/", 1)[-1]
            if name:
                return name
    raise AssertionError(
        "could not read the ledger filename out of %s — the assignment this "
        "test reads it from has changed shape" % TRACKER_SH
    )


LEDGER_FILENAME = _ledger_filename()


def _git(cwd, *args, env=None):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        check=True, env=env,
    )


def _scratch_env(home: Path, extra=None):
    """A subprocess environment that CANNOT reach the real private repository.

    HOME is redirected because anchor-tracker.sh's discovery chain falls back
    to a path under ``$HOME``.  A test that left the real HOME in place would
    be one bug away from writing to the real private repository — which is the
    exact accident this whole test file exists because of.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_AUTHOR_NAME"] = "Gate Test"
    env["GIT_AUTHOR_EMAIL"] = "gate-test@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Gate Test"
    env["GIT_COMMITTER_EMAIL"] = "gate-test@example.invalid"
    env.pop(COAUTHOR_ENV, None)
    env.pop("INTERGENOS_PRIVATE_REPO", None)
    env.pop("INTERGENOS_PUBLIC_REPO", None)
    if extra:
        env.update(extra)
    return env


class TestRemoteUrlMatcher(unittest.TestCase):
    """The matcher decides, by URL, whether a push is a real promotion."""

    # Every spelling git will hand a hook for the real publication target.
    ACCEPT = [
        "git@github.com:InterGenJLU/intergenos.git",
        "git@github.com:InterGenJLU/intergenos",
        "https://github.com/InterGenJLU/intergenos.git",
        "https://github.com/InterGenJLU/intergenos",
        "ssh://git@github.com/InterGenJLU/intergenos.git",
        "git://github.com/InterGenJLU/intergenos.git",
        "https://github.com/InterGenJLU/intergenos/",
    ]

    # Each of these MUST NOT anchor.  The first two are the shape of the push
    # that caused the incident; the rest are near-misses a prefix or substring
    # test would wave through.
    REJECT = [
        "/home/someone/scratch/bare-public.git",
        "file:///tmp/bare-public.git",
        "../bare-public.git",
        # A repository whose path EXTENDS the real one. This is the near-miss a
        # prefix test waves through, so it is built from the accepted origin
        # plus a suffix rather than spelled out: what matters is the shape, and
        # deriving it says so exactly. Both URL forms, since the two normalise
        # by different paths.
        "git@github.com:" + _ORIGIN_PATH + "-sibling.git",
        "https://github.com/" + _ORIGIN_PATH + "-sibling.git",
        "git@github.com:SomeoneElse/intergenos.git",
        "git@gitlab.com:InterGenJLU/intergenos.git",
        "git@github.com.example.invalid:InterGenJLU/intergenos.git",
        "https://github.com.example.invalid/InterGenJLU/intergenos.git",
        "https://example.invalid/github.com/InterGenJLU/intergenos.git",
        "",
    ]

    def test_matcher_script_exists_and_is_executable(self):
        self.assertTrue(MATCHER.is_file(), f"{MATCHER} missing")
        self.assertTrue(
            MATCHER.stat().st_mode & 0o111,
            f"{MATCHER} is not executable — the hook invokes it directly",
        )

    def test_accepts_every_spelling_of_the_public_origin(self):
        for url in self.ACCEPT:
            with self.subTest(url=url):
                r = subprocess.run(
                    [str(MATCHER), url], capture_output=True, text=True,
                )
                self.assertEqual(
                    r.returncode, 0,
                    "the real publication target was not recognised: %r\n"
                    "stdout=%r stderr=%r" % (url, r.stdout, r.stderr),
                )

    def test_rejects_every_target_that_is_not_the_public_origin(self):
        for url in self.REJECT:
            with self.subTest(url=url):
                r = subprocess.run(
                    [str(MATCHER), url], capture_output=True, text=True,
                )
                self.assertNotEqual(
                    r.returncode, 0,
                    "a push target that is NOT the publication origin was "
                    "accepted, so the anchor would be written for it: %r\n"
                    "stdout=%r stderr=%r" % (url, r.stdout, r.stderr),
                )

    def test_missing_argument_is_an_invocation_error_not_a_match(self):
        """No argument must never read as "this is the origin".

        A refusal and an invocation error must be distinguishable, or a caller
        cannot tell "not the origin" from "I was called wrongly".
        """
        r = subprocess.run([str(MATCHER)], capture_output=True, text=True)
        self.assertEqual(
            r.returncode, 2,
            "calling the matcher with no URL should be invocation error 2, "
            "got rc=%d stdout=%r stderr=%r" % (r.returncode, r.stdout, r.stderr),
        )


class TestPrePushHookRoutesTheAnchorThroughTheMatcher(unittest.TestCase):
    """The hook must read the remote URL git gives it, and act on it."""

    def setUp(self):
        self.text = PRE_PUSH.read_text()

    def test_hook_captures_the_remote_url_positional_argument(self):
        self.assertRegex(
            self.text,
            r'PUSH_REMOTE_URL=\$\{2:-\}',
            "the pre-push hook does not capture $2 (the remote URL git passes "
            "it). Without it the anchor gate cannot tell a real promotion from "
            "a push at a throwaway remote.",
        )

    def test_hook_does_not_decide_by_remote_name(self):
        """The remote NAME is attacker-free but not evidence.

        Any clone may name any remote "origin"; the throwaway clone in the
        incident did exactly that. A decision keyed on $1 would still have
        written the anchor.
        """
        anchor_block = self.text.split("---- 10.")[-1]
        self.assertNotRegex(
            anchor_block,
            r'PUSH_REMOTE_NAME"?\s*=\s*"?origin',
            "the anchor gate compares the remote NAME against 'origin' — a "
            "throwaway clone can name a local bare remote 'origin', which is "
            "how the false anchor was written. Decide on the URL.",
        )

    def test_anchor_gate_invokes_the_matcher(self):
        anchor_block = self.text.split("---- 10.")[-1]
        self.assertIn(
            "anchor-remote-is-public-origin.sh", anchor_block,
            "the anchor gate does not consult the remote-URL matcher, so it "
            "still fires on the ref name alone",
        )

    def test_anchor_gate_reports_the_no_anchor_decision(self):
        """A gate that silently declines is indistinguishable from one that ran."""
        anchor_block = self.text.split("---- 10.")[-1]
        self.assertIn(
            "not the public origin", anchor_block,
            "the anchor gate does not print a line when it declines to "
            "anchor; a silent decline cannot be read back from a capture",
        )


class _TrackerHarness(unittest.TestCase):
    """Two throwaway repositories and a contained bare remote for the private one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anchor-gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = _scratch_env(self.home)

        # Throwaway public repository — one commit, so HEAD resolves.
        self.public = self.tmp / "public"
        self.public.mkdir()
        _git(self.public, "init", "-q", "-b", "master", env=self.env)
        (self.public / "README.md").write_text("scratch public repo\n")
        _git(self.public, "add", "README.md", env=self.env)
        _git(self.public, "commit", "-qm", "initial", env=self.env)
        self.public_head = _git(
            self.public, "rev-parse", "HEAD", env=self.env
        ).stdout.strip()
        self.public_short = _git(
            self.public, "rev-parse", "--short=8", "HEAD", env=self.env
        ).stdout.strip()

        # Throwaway private repository with an ANCHOR line, and a LOCAL bare
        # remote so a real push in this test cannot leave the scratch tree.
        self.private_bare = self.tmp / "private-bare.git"
        _git(self.tmp, "init", "-q", "--bare", str(self.private_bare), env=self.env)
        self.private = self.tmp / "private"
        self.private.mkdir()
        _git(self.private, "init", "-q", "-b", "master", env=self.env)
        self.tracker = self.private / LEDGER_FILENAME
        self.tracker.write_text(
            "# scratch tracker\n\n"
            "<!-- ANCHOR: public-master HEAD deadbeef -->\n"
        )
        _git(self.private, "add", LEDGER_FILENAME, env=self.env)
        _git(self.private, "commit", "-qm", "initial", env=self.env)
        _git(self.private, "remote", "add", "origin", str(self.private_bare),
             env=self.env)
        _git(self.private, "push", "-q", "-u", "origin", "master", env=self.env)
        self.private_head_before = _git(
            self.private, "rev-parse", "HEAD", env=self.env
        ).stdout.strip()

        self.env["INTERGENOS_PUBLIC_REPO"] = str(self.public)
        self.env["INTERGENOS_PRIVATE_REPO"] = str(self.private)

    def run_tracker(self, *args, coauthor=None, dry_run_env=False):
        env = dict(self.env)
        if coauthor is not None:
            env[COAUTHOR_ENV] = coauthor
        if dry_run_env:
            env["ANCHOR_TRACKER_DRY_RUN"] = "1"
        return subprocess.run(
            [str(TRACKER_SH), *args],
            capture_output=True, text=True, env=env, cwd=str(self.tmp),
        )

    def private_head(self):
        return _git(self.private, "rev-parse", "HEAD", env=self.env).stdout.strip()


class TestAnchorTrackerCoauthorTrailer(_TrackerHarness):
    def test_refuses_when_the_coauthor_variable_is_unset(self):
        r = self.run_tracker(self.public_head)
        self.assertNotEqual(
            r.returncode, 0,
            "the anchor commit was made with no stated co-author. The trailer "
            "is a disclosure of who did the work; a script that has not been "
            "told must refuse, not guess.\nstdout=%r stderr=%r"
            % (r.stdout, r.stderr),
        )
        self.assertIn(
            COAUTHOR_ENV, r.stdout + r.stderr,
            "the refusal does not name the variable that was unset, so the "
            "operator cannot act on it",
        )
        self.assertEqual(
            self.private_head(), self.private_head_before,
            "the private repository was committed to despite the refusal",
        )

    def test_refuses_a_malformed_coauthor_value(self):
        r = self.run_tracker(self.public_head, coauthor="not-a-trailer")
        self.assertNotEqual(
            r.returncode, 0,
            "a co-author value with no address was accepted and stamped into "
            "a commit trailer\nstdout=%r stderr=%r" % (r.stdout, r.stderr),
        )
        self.assertEqual(self.private_head(), self.private_head_before)

    def test_uses_the_stated_coauthor_and_no_other(self):
        stated = "Test Author <noreply@example.invalid>"
        r = self.run_tracker(self.public_head, coauthor=stated)
        self.assertEqual(
            r.returncode, 0,
            "the anchor did not advance with a valid co-author stated\n"
            "stdout=%r stderr=%r" % (r.stdout, r.stderr),
        )
        msg = _git(
            self.private, "log", "-1", "--format=%B", env=self.env
        ).stdout
        trailers = [
            line.strip() for line in msg.splitlines()
            if line.strip().lower().startswith("co-authored-by:")
        ]
        # Exactly one, and it is the stated author. Asserting the SHAPE rather
        # than the absence of one particular old literal is both stronger and
        # durable: it fails for any unstated author, not just the one that used
        # to be compiled in.
        self.assertEqual(
            len(trailers), 1,
            "the anchor commit should carry exactly one co-author trailer, "
            "got %d: %r" % (len(trailers), trailers),
        )
        self.assertEqual(
            trailers[0], "Co-Authored-By: " + stated,
            "the anchor commit's co-author is not the one that was stated",
        )

    def test_the_script_composes_the_trailer_from_a_variable(self):
        """No co-author line in the script may carry a name of its own.

        Checked structurally rather than against one known-bad literal: any
        trailer the script writes must interpolate the value it was given, so a
        different hard-coded name could not slip back in unnoticed.
        """
        body = TRACKER_SH.read_text()
        trailer_lines = [
            line for line in body.splitlines()
            if line.strip().lower().startswith("co-authored-by:")
        ]
        self.assertTrue(
            trailer_lines,
            "no co-author trailer line found in %s at all — the extraction "
            "found nothing, so this check would pass vacuously" % TRACKER_SH,
        )
        for line in trailer_lines:
            with self.subTest(line=line.strip()):
                self.assertIn(
                    "${COAUTHOR}", line,
                    "a co-author trailer in the script names an author of its "
                    "own instead of interpolating the stated one: %r"
                    % line.strip(),
                )
        self.assertIn(
            COAUTHOR_ENV, body,
            "the script does not read %s" % COAUTHOR_ENV,
        )


class TestAnchorTrackerDryRun(_TrackerHarness):
    def test_dry_run_changes_nothing_and_prints_what_it_would_do(self):
        r = self.run_tracker(
            "--dry-run", self.public_head,
            coauthor="Test Author <noreply@example.invalid>",
        )
        self.assertEqual(
            r.returncode, 0,
            "--dry-run is not accepted\nstdout=%r stderr=%r"
            % (r.stdout, r.stderr),
        )
        out = r.stdout + r.stderr
        self.assertIn(
            self.public_short, out,
            "the dry run does not print the anchor value it would write",
        )
        self.assertIn(
            str(self.private), out,
            "the dry run does not name the repository it would commit to",
        )
        self.assertEqual(
            self.private_head(), self.private_head_before,
            "--dry-run committed to the private repository",
        )
        self.assertIn(
            "deadbeef", self.tracker.read_text(),
            "--dry-run rewrote the ledger file on disk",
        )

    def test_environment_variable_rehearses_exactly_like_the_flag(self):
        """The gate cannot pass a flag, so the variable must do the same job.

        The pre-push gate invokes this script with a fixed argument list. If
        only the flag rehearsed, the gate itself could never be proved without
        a real outward write — which is the thing being guarded against.
        """
        env_run = self.run_tracker(
            self.public_head,
            coauthor="Test Author <noreply@example.invalid>",
            dry_run_env=True,
        )
        self.assertEqual(
            env_run.returncode, 0,
            "ANCHOR_TRACKER_DRY_RUN=1 was not honoured\nstdout=%r stderr=%r"
            % (env_run.stdout, env_run.stderr),
        )
        self.assertIn("DRY RUN", env_run.stdout + env_run.stderr)
        self.assertEqual(
            self.private_head(), self.private_head_before,
            "the environment rehearsal committed to the private repository",
        )

    def test_dry_run_still_refuses_without_a_stated_coauthor(self):
        """A rehearsal must refuse for the same reasons the real run would.

        A dry run that passes where the real run would refuse is a rehearsal
        of a different script.
        """
        r = self.run_tracker("--dry-run", self.public_head)
        self.assertNotEqual(
            r.returncode, 0,
            "--dry-run reported success for a run the real path would refuse\n"
            "stdout=%r stderr=%r" % (r.stdout, r.stderr),
        )
        # A bare "it exited non-zero" would pass VACUOUSLY before --dry-run
        # exists at all: today the flag is parsed as a revision and git dies
        # with "Needed a single revision", which is a different refusal for a
        # different reason. Pin the REASON so this can only pass once the
        # rehearsal path genuinely reaches the co-author check.
        self.assertIn(
            COAUTHOR_ENV, r.stdout + r.stderr,
            "--dry-run refused, but not for the stated-co-author reason; it "
            "refused before reaching that check\nstdout=%r stderr=%r"
            % (r.stdout, r.stderr),
        )


if __name__ == "__main__":
    unittest.main()

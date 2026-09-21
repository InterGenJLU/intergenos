"""The pre-push rewrite and staleness checks must judge the remote being pushed to.

WHY THIS TEST EXISTS (measured 2026-09-20, in this repository)
--------------------------------------------------------------
This project publishes to two remotes.  Gate 2 of ``.githooks/pre-push`` -- the
check that a branch is not behind its remote counterpart, and the check that
refuses an undeclared history rewrite -- was written when there was one.  It
asked ``origin`` whether the branch existed, fetched ``origin``, and compared
the local head against ``refs/remotes/origin/<branch>``, whichever remote the
push was actually going to.  git hands a pre-push hook the remote NAME as
``$1`` and its URL as ``$2``, and this hook already captures both near the top
for a different gate; gate 2 read neither.

That is not a cosmetic wrong label.  Measured in the sandbox this file builds:

* push a branch to both remotes, rewrite it, declare the rewrite to the FIRST
  remote and push it there.  The local head now equals ``origin/<branch>``.
* push the same rewrite to the SECOND remote with ``--force-with-lease`` and
  NO declaration.  The old gate compared the local head against the FIRST
  remote, found them equal, never entered the divergence arm, printed
  "all gates PASS", and the second remote's history was overwritten.
* the identical push aimed at the FIRST remote is refused, by name.

The same substitution makes a branch that is BEHIND the second remote look
current, so a forced push rewinds that remote instead of being stopped.  A
check that answers about the wrong remote has not checked anything, and it
reports success while doing it.

WHAT IS ASSERTED
----------------
Every check runs the REAL hook through a REAL ``git push`` at two real (local,
bare) remotes.  Nothing here re-implements the hook's logic, and nothing here
reads the hook's source to decide whether it is correct: the assertions are
about what the hook SAYS and what the remotes CONTAIN afterwards.

* an undeclared rewrite pushed to the second remote is refused, the refusal
  names the second remote, and that remote's history is unchanged;
* the identical push at the first remote is refused too -- the control that
  proves the harness can tell the two outcomes apart;
* a declared rewrite at the second remote is accepted with a warning naming it;
* a branch behind the second remote is refused as behind THAT remote;
* a branch absent from the second remote is reported as new THERE, not judged
  against the first;
* the second push of an unchanged commit runs the SAME named gates as the
  first -- the quieter half of the same defect, measured the same day: the
  baseline the content gates validate against was also derived from origin
  alone, so on the second push the commit was found "already published", the
  validation range collapsed to empty, and two gates that announce themselves
  by name (the release-note chain gate and the changelog accumulation gate)
  did not run and said nothing about not running.  The push printed
  "all gates PASS" having run fewer of them;
* ``master`` and ``dev`` are not rewritable on a remote that is not ``origin``.

HOW THE SANDBOX IS BUILT, AND WHY IT IS NOT A CLONE OF THIS REPOSITORY
----------------------------------------------------------------------
Gate 2 runs after four gates that scan the tree, and on a full checkout those
take about seventy-five seconds per push -- seven pushes would be nine minutes
of suite time to exercise one gate.  So the sandbox is a SMALL repository that
carries the real ``.githooks``, the real ``scripts`` and the real ``config``,
which is everything the earlier gates need to run for real.  They do run, on
every push here, and they pass on their own merits; they are simply given a
few hundred files to read instead of several thousand.

Three details of that sandbox are worth naming, because each one is a place a
careless harness would go quiet instead of failing:

* the two private list files the content and language gates require are the
  gates' own documented environment overrides, written here with entries that
  match nothing.  A missing list makes those gates refuse, which is correct of
  them and would mask this gate;
* the license-spelling gate refuses when an exemption names a file that is not
  present.  The files to copy are read from that gate's own
  ``--list-exemptions`` output rather than listed here, so a change to the
  exemptions cannot leave this file quietly stale;
* every push that is only scaffolding is made with ``core.hooksPath`` emptied.
  The only pushes that run the hook are the ones under test.
"""

import os
import re
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
PRE_PUSH = REPO / ".githooks" / "pre-push"
LICENSE_GATE = REPO / "scripts" / "check-license-spelling.py"

# The sandbox's two remotes.  Every assertion about "the remote being pushed
# to" is written against these names, so a test cannot pass merely because the
# hook printed the word "origin".
FIRST = "origin"
SECOND = "hub"

# Directories copied into the sandbox: the hook under test, and everything the
# gates that run before it need in order to run for real.
_SANDBOX_DIRS = (".githooks", "scripts", "config")


class _TwoRemoteSandbox(unittest.TestCase):
    """A small real repository with two real bare remotes and the real hook."""

    @classmethod
    def setUpClass(cls) -> None:
        for required in (PRE_PUSH, LICENSE_GATE):
            if not required.is_file():
                raise unittest.SkipTest(f"{required} is absent")

        # Beside the repository, not under the system temporary directory:
        # this project keeps git trees off the temporary filesystem.
        cls.tmp = Path(tempfile.mkdtemp(prefix=".pre-push-two-remotes-", dir=str(REPO.parent)))
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
        cls._write_private_lists()
        cls.env = cls._sandbox_env()

        cls.first_bare = cls.tmp / "first.git"
        cls.second_bare = cls.tmp / "second.git"
        for bare in (cls.first_bare, cls.second_bare):
            cls._git(cls.tmp, "init", "-q", "--bare", "-b", "master", str(bare))

        cls.work = cls.tmp / "work"
        cls.work.mkdir()
        cls._git(cls.work, "init", "-q", "-b", "master", ".")
        for name in _SANDBOX_DIRS:
            shutil.copytree(REPO / name, cls.work / name, symlinks=True)
        cls._copy_the_files_the_license_exemptions_name()
        cls._git(cls.work, "config", "user.name", "Gate Test")
        cls._git(cls.work, "config", "user.email", "gate-test@example.invalid")
        (cls.work / "marker.txt").write_text("seed\n", encoding="utf-8")
        cls._git(cls.work, "add", "-A")
        # -n skips the pre-COMMIT hook: this file tests the pre-PUSH hook, and
        # the sandbox's commits are scaffolding, not the thing under test.
        cls._git(cls.work, "commit", "-qn", "-m", "chore(test): the sandbox tree, seeded once")

        cls._git(cls.work, "remote", "add", FIRST, str(cls.first_bare))
        cls._git(cls.work, "remote", "add", SECOND, str(cls.second_bare))
        cls._git(cls.work, "branch", "dev")
        # Scaffolding pushes do not run the hook; only the pushes under test do.
        for remote in (FIRST, SECOND):
            cls._git(cls.work, "-c", "core.hooksPath=", "push", "-q", remote, "master", "dev")
        cls._git(cls.work, "config", "core.hooksPath", ".githooks")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- sandbox construction ---------------------------------------------

    @classmethod
    def _write_private_lists(cls) -> None:
        """The gates' own documented overrides, with entries that match nothing.

        These two gates refuse when their list is missing, which is right of
        them: a missing list is never a silent pass.  Supplying a real list
        that matches nothing lets them run and pass on their own terms without
        this file needing to know anything private.
        """
        cls.denylist = cls.tmp / "language-denylist"
        cls.denylist.write_text("zzzz-a-term-that-appears-nowhere\n", encoding="utf-8")
        cls.patterns = cls.tmp / "content-patterns"
        cls.patterns.write_text(
            "[AGENT_NAMES]\nnever\tzzzz-no-such-name\n"
            "[AGENT_ABBREV]\nnever\tzzzz-no-such-abbreviation\n"
            "[HOME_PATH]\nnever\tzzzz-no-such-home-path\n"
            "[FLEET_HOST_BLOCK]\nnever\tzzzz-no-such-host\n",
            encoding="utf-8",
        )

    @classmethod
    def _copy_the_files_the_license_exemptions_name(cls) -> None:
        """Read the exempted paths from the gate itself, then copy them.

        Listing them here instead would rot the day an exemption changes, and
        rot in a fixture is how a gate ends up untested.
        """
        listing = subprocess.run(
            ["python3", str(LICENSE_GATE), "--list-exemptions"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        named = re.findall(r"^[a-z0-9-]+: (\S+) ", listing.stdout, re.M)
        for rel in sorted(set(named)):
            source = REPO / rel
            if not source.is_file():
                continue
            target = cls.work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)

    @classmethod
    def _sandbox_env(cls, extra=None) -> dict:
        """A subprocess environment that cannot reach this machine's real setup.

        The real configuration names real remotes and a real identity.  A hook
        test that left it in place would be one typo away from fetching from,
        or pushing to, the real publication target.
        """
        env = dict(os.environ)
        env["HOME"] = str(cls.home)
        env["GIT_CONFIG_GLOBAL"] = str(cls.home / ".gitconfig")
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_AUTHOR_NAME"] = "Gate Test"
        env["GIT_AUTHOR_EMAIL"] = "gate-test@example.invalid"
        env["GIT_COMMITTER_NAME"] = "Gate Test"
        env["GIT_COMMITTER_EMAIL"] = "gate-test@example.invalid"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["IGOS_PUBLIC_LANGUAGE_DENYLIST"] = str(cls.denylist)
        env["IGOS_PUBLIC_CONTENT_PATTERNS"] = str(cls.patterns)
        env.pop("GIT_PRE_PUSH_ALLOW_REWRITE", None)
        env.pop("GIT_PRE_PUSH_SKIP_FETCH", None)
        if extra:
            env.update(extra)
        return env

    @classmethod
    def _git(cls, cwd, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            check=check, env=getattr(cls, "env", None),
        )

    # -- per-test helpers --------------------------------------------------

    def _branch_on_both_remotes(self, name: str) -> str:
        self._git(self.work, "checkout", "-q", "master")
        self._git(self.work, "checkout", "-q", "-B", name)
        self._write_commit(f"{name} first")
        for remote in (FIRST, SECOND):
            self._git(self.work, "-c", "core.hooksPath=", "push", "-q", remote, name)
        return name

    def _write_commit(self, text: str, amend: bool = False) -> str:
        (self.work / "marker.txt").write_text(text + "\n", encoding="utf-8")
        self._git(self.work, "add", "marker.txt")
        args = ["commit", "-qn", "-m", "chore(test): a sandbox commit"]
        if amend:
            args.insert(1, "--amend")
        self._git(self.work, *args)
        return self._git(self.work, "rev-parse", "HEAD").stdout.strip()

    def _advance_the_remote(self, bare: Path, branch: str) -> str:
        """Move a remote's branch on without touching the working clone."""
        other = self.tmp / "mover"
        shutil.rmtree(other, ignore_errors=True)
        self._git(self.tmp, "clone", "--quiet", str(bare), str(other))
        self._git(other, "checkout", "-q", branch)
        (other / "marker.txt").write_text("the remote moved on\n", encoding="utf-8")
        self._git(other, "add", "marker.txt")
        self._git(other, "commit", "-qn", "-m", "chore(test): the remote moves on")
        head = self._git(other, "rev-parse", "HEAD").stdout.strip()
        self._git(other, "-c", "core.hooksPath=", "push", "-q", "origin", branch)
        shutil.rmtree(other, ignore_errors=True)
        # The working clone must know the new remote head, or --force-with-lease
        # refuses on its own and the hook is never reached.
        remote = FIRST if bare == self.first_bare else SECOND
        self._git(self.work, "fetch", "-q", remote, branch)
        return head

    def _push(self, remote: str, branch: str, declared: bool = False, force: bool = False):
        env = self._sandbox_env(
            {"GIT_PRE_PUSH_ALLOW_REWRITE": "1"} if declared else None
        )
        args = ["git", "push"]
        if force:
            args.append("--force")
        args += [remote, branch]
        return subprocess.run(args, cwd=str(self.work), capture_output=True,
                              text=True, env=env)

    def _remote_head(self, bare: Path, branch: str) -> str:
        out = self._git(self.tmp, "ls-remote", str(bare), f"refs/heads/{branch}").stdout
        return out.split("\t")[0] if out.strip() else ""

    @staticmethod
    def _gates_named(result) -> set:
        """Every gate that announced itself by name in this push's output.

        Two shapes appear: a bracketed tag at the start of a line, as in
        ``[register] PASS: ...``, and a gate that names itself in prose, as in
        ``release-note chain gate: PASS``.  Both are collected, because a gate
        that stops running stops printing either one, and the whole point here
        is that a gate which does not run must not be invisible.
        """
        blob = (result.stdout or "") + (result.stderr or "")
        found = set()
        for raw in blob.splitlines():
            line = raw.strip()
            tag = re.match(r"^\[([a-z][a-z-]*)\]", line)
            if tag and tag.group(1) != "pre-push":
                found.add(tag.group(1))
            named = re.match(r"^([a-z][a-z -]*gate): ", line)
            if named:
                found.add(named.group(1))
        return found

    @staticmethod
    def _hook_said(result) -> str:
        """Only what the HOOK printed, never git's own transport messages.

        An assertion over the whole output could pass on git's
        "non-fast-forward" line while the hook said nothing at all, which is
        precisely the defect this file exists for.
        """
        blob = (result.stdout or "") + (result.stderr or "")
        kept, in_hook = [], False
        for raw in blob.splitlines():
            line = raw.strip()
            if line.startswith("[pre-push]"):
                kept.append(line)
                in_hook = True
            elif in_hook and line.startswith((
                "Remote at:", "Local at:", "Old remote head:", "New local head:",
                "Rebase:", "Resolve:", "Authorized rewrite", "All remaining gates",
            )):
                kept.append(line)
            elif not line:
                in_hook = False
        return "\n".join(kept)


class TestTheSecondRemoteIsJudgedAsItself(_TwoRemoteSandbox):

    def test_an_undeclared_rewrite_is_refused_on_the_second_remote(self):
        """The measured defect: this push silently overwrote the second remote."""
        branch = self._branch_on_both_remotes("feature/undeclared-rewrite-second")
        before = self._remote_head(self.second_bare, branch)

        # Rewrite, and declare it to the FIRST remote only.  The local head now
        # matches the first remote, which is the state that made the old check
        # ask the wrong question and find nothing wrong.
        self._write_commit("rewritten", amend=True)
        declared = self._push(FIRST, branch, declared=True, force=True)
        self.assertEqual(declared.returncode, 0, declared.stdout + declared.stderr)

        r = self._push(SECOND, branch, force=True)
        said = self._hook_said(r)
        self.assertIn("diverged", said,
                      "the hook said nothing about rewriting the second remote's "
                      f"branch. It printed:\n{said or '(nothing at all)'}")
        self.assertIn(SECOND, said,
                      f"the refusal did not name the remote being pushed to:\n{said}")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._remote_head(self.second_bare, branch), before,
                         "the second remote's history was overwritten")

    def test_the_same_undeclared_rewrite_is_refused_on_the_first_remote(self):
        """The control: the harness can tell a refusal from a silent pass."""
        branch = self._branch_on_both_remotes("feature/undeclared-rewrite-first")
        before = self._remote_head(self.first_bare, branch)

        self._write_commit("rewritten", amend=True)
        r = self._push(FIRST, branch, force=True)
        said = self._hook_said(r)
        self.assertIn("diverged", said, said or "(nothing at all)")
        self.assertIn(FIRST, said, said)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self._remote_head(self.first_bare, branch), before)

    def test_a_declared_rewrite_is_accepted_on_the_second_remote_and_named(self):
        branch = self._branch_on_both_remotes("feature/declared-rewrite-second")
        self._write_commit("rewritten", amend=True)
        head = self._git(self.work, "rev-parse", "HEAD").stdout.strip()

        r = self._push(SECOND, branch, declared=True, force=True)
        said = self._hook_said(r)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("declared rewrite", said,
                      "a declared rewrite of the second remote produced no warning:\n"
                      f"{said or '(nothing at all)'}")
        self.assertIn(SECOND, said,
                      f"the rewrite warning did not name that remote:\n{said}")
        self.assertEqual(self._remote_head(self.second_bare, branch), head)

    def test_a_branch_behind_the_second_remote_is_refused_as_behind_it(self):
        branch = self._branch_on_both_remotes("feature/behind-the-second")
        moved = self._advance_the_remote(self.second_bare, branch)

        r = self._push(SECOND, branch, force=True)
        said = self._hook_said(r)
        self.assertIn("behind", said,
                      "the hook did not notice the branch was behind the remote it "
                      f"was pushed to:\n{said or '(nothing at all)'}")
        self.assertIn(SECOND, said,
                      f"the report did not name that remote:\n{said}")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self._remote_head(self.second_bare, branch), moved,
                         "the second remote was rewound")

    def test_a_branch_new_to_the_second_remote_is_reported_as_new_there(self):
        branch = "feature/new-to-the-second-remote"
        self._git(self.work, "checkout", "-q", "master")
        self._git(self.work, "checkout", "-q", "-B", branch)
        self._write_commit(f"{branch} only on the first remote")
        self._git(self.work, "-c", "core.hooksPath=", "push", "-q", FIRST, branch)

        r = self._push(SECOND, branch)
        said = self._hook_said(r)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("brand-new branch", said,
                      "a branch absent from the remote being pushed to was not "
                      f"reported as new there:\n{said or '(nothing at all)'}")
        self.assertIn(SECOND, said,
                      f"the brand-new-branch line did not name that remote:\n{said}")

    def test_a_branch_behind_the_first_remote_is_still_refused(self):
        """No regression: the first remote keeps the behaviour it always had."""
        branch = self._branch_on_both_remotes("feature/behind-the-first")
        moved = self._advance_the_remote(self.first_bare, branch)

        r = self._push(FIRST, branch, force=True)
        said = self._hook_said(r)
        self.assertIn("behind", said, said or "(nothing at all)")
        self.assertIn(FIRST, said, said)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self._remote_head(self.first_bare, branch), moved)


class TestBothPushesOfOneCommitRunTheSameGates(_TwoRemoteSandbox):
    """A second push must not be a quieter push."""

    def test_the_second_push_of_an_unchanged_commit_runs_the_same_named_gates(self):
        branch = "feature/same-gates-on-both-remotes"
        self._git(self.work, "checkout", "-q", "master")
        self._git(self.work, "checkout", "-q", "-B", branch)
        self._write_commit("one commit, pushed to both remotes unchanged")

        first = self._push(FIRST, branch)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self._push(SECOND, branch)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        gates_first = self._gates_named(first)
        gates_second = self._gates_named(second)
        self.assertTrue(gates_first, "the first push named no gates at all; "
                                     "the harness is not reading the output")
        missing = gates_first - gates_second
        self.assertEqual(
            set(), missing,
            "the second push of the same commit ran fewer named gates than the "
            f"first. Missing on the push to {SECOND}: {sorted(missing)}.\n"
            f"first push named:  {sorted(gates_first)}\n"
            f"second push named: {sorted(gates_second)}",
        )

    def test_both_pushes_name_the_two_gates_the_defect_silenced(self):
        """Named outright, so a rename cannot turn this back into silence."""
        branch = "feature/the-two-gates-that-went-quiet"
        self._git(self.work, "checkout", "-q", "master")
        self._git(self.work, "checkout", "-q", "-B", branch)
        self._write_commit("one commit, pushed to both remotes unchanged")

        for remote in (FIRST, SECOND):
            r = self._push(remote, branch)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            named = self._gates_named(r)
            for gate in ("release-note chain gate", "changelog accumulation gate"):
                self.assertIn(gate, named,
                              f"the push to {remote} did not run {gate}; it named "
                              f"{sorted(named)}")


class TestARemoteThatHoldsNoDevIsStillPushedTo(_TwoRemoteSandbox):
    """A remote may legitimately have no dev branch at all.

    A fresh bare mirror, a backup remote, or a push named by URL whose dev tip
    is not among the local objects all answer "no dev head". The hook asks the
    remote being pushed to for its dev and master heads in order to pick a
    baseline; "there is none" is an ANSWER to that question, not a failure of
    it. Found 2026-09-21 by the second reader of this lane: the helper ended
    in an && chain, so with no usable head it returned non-zero, and under the
    file's `set -euo pipefail` the assignment that called it ended the hook
    there — one line of git's own generic wording, no gate result, no reason.
    A hook that stops without saying why is the exact failure this project
    treats as worst: it is indistinguishable from a gate refusing.
    """

    def _remote_holding_no_dev(self, name: str) -> Path:
        bare = self.tmp / f"{name}.git"
        shutil.rmtree(bare, ignore_errors=True)
        self._git(self.tmp, "init", "-q", "--bare", "-b", "master", str(bare))
        self._git(self.work, "remote", "add", name, str(bare))
        self.addCleanup(lambda: self._git(self.work, "remote", "remove", name,
                                          check=False))
        self.addCleanup(lambda: shutil.rmtree(bare, ignore_errors=True))
        return bare

    def test_a_brand_new_branch_reaches_a_remote_that_holds_no_dev(self):
        bare = self._remote_holding_no_dev("emptyremote")
        branch = "feature/first-push-to-an-empty-remote"
        self._git(self.work, "checkout", "-q", "master")
        self._git(self.work, "checkout", "-q", "-B", branch)
        head = self._write_commit("the first commit this remote has ever seen")

        r = self._push("emptyremote", branch)
        said = self._hook_said(r)
        named = self._gates_named(r)
        self.assertEqual(
            r.returncode, 0,
            "the push to a remote holding no dev did not complete.\n"
            f"the hook said: {said or '(nothing at all)'}\n"
            f"full output:\n{r.stdout}{r.stderr}")
        self.assertTrue(
            named,
            "the push to a remote holding no dev named no gate at all, so the "
            "hook stopped before it ran any: "
            f"{said or '(the hook printed nothing)'}")
        for gate in ("release-note chain gate", "changelog accumulation gate"):
            self.assertIn(gate, named,
                          f"the push to a remote holding no dev did not run "
                          f"{gate}; it named {sorted(named)}")
        self.assertEqual(self._remote_head(bare, branch), head,
                         "the branch did not arrive on the remote")


class TestProtectedBranchesAreProtectedOnEveryRemote(_TwoRemoteSandbox):
    """master and dev are never rewritable, on any remote, declared or not."""

    def test_master_is_not_rewritable_on_a_remote_that_is_not_origin(self):
        self._a_protected_rewrite_is_refused("master")

    def test_dev_is_not_rewritable_on_a_remote_that_is_not_origin(self):
        self._a_protected_rewrite_is_refused("dev")

    def _a_protected_rewrite_is_refused(self, protected: str) -> None:
        self._git(self.work, "checkout", "-q", protected)
        self._write_commit(f"{protected} moves")
        for remote in (FIRST, SECOND):
            self._git(self.work, "-c", "core.hooksPath=", "push", "-q", remote, protected)
        before = self._remote_head(self.second_bare, protected)

        self._write_commit(f"{protected} rewritten", amend=True)
        r = self._push(SECOND, protected, declared=True, force=True)
        self.assertNotEqual(r.returncode, 0,
                            f"a rewrite of {protected} was accepted on the second "
                            f"remote:\n{r.stdout}{r.stderr}")
        self.assertEqual(self._remote_head(self.second_bare, protected), before,
                         f"{protected} was overwritten on the second remote")


if __name__ == "__main__":
    unittest.main()

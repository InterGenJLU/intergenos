"""The pre-push hook reads a long commit message, file or file list whole.

WHY THIS TEST EXISTS (measured 2026-09-22, in this repository)
--------------------------------------------------------------
``.githooks/pre-push`` runs under ``set -euo pipefail``.  Under pipefail a
pipeline fails when its writer is killed by SIGPIPE, and that happens
whenever the reader exits before the writer has finished: ``grep -q`` stops at
its first match, ``head -1`` after one line.  Input longer than a pipe holds
is enough to reach it -- a long commit message, a large recipe, a long list of
changed files.  Measured on a 177 KB message:

* the conventional-commit gate took the subject with
  ``subject=$(echo "$msg" | head -1)``; the assignment failed with status 141
  and ``set -e`` ended the hook on the spot -- no gate result, no reason, and
  nothing published;
* ``if echo "$msg" | grep -qE '^NO-GATE:'`` read a NO-GATE line near the top
  as "no match", so a commit that declared its exemption was refused by the
  gate it was exempt from;
* the scope gate read a file named early in the message as "not named" and
  refused the commit for under-documentation;
* the install-recursion gate asks two questions of a recipe with
  ``echo "$content" | grep -q``; on a build.sh larger than a pipe holds, the
  first match turned into "no match" and the recipe the gate exists to stop
  was PUBLISHED -- the one case in this class that fails open;
* the deferral gate read the ledger document near the top of a long changed
  file list as "not modified" and refused a commit that had registered its
  deferral.

WHAT IS ASSERTED
----------------
One static invariant over the hook's own text -- no pipeline in the file ends
in a reader that stops early -- so the class cannot come back one line at a
time; and real pushes, through the REAL hook, to a real (local, bare) remote,
each asserting what the hook SAYS and what the remote CONTAINS afterwards: a
long input is published or refused with its gate's reason, and each short
control shows the same gate answering the same way as it always did.

HOW THE SANDBOX IS BUILT
------------------------
As in test_pre_push_checks_read_the_remote_being_pushed_to.py: a small
repository carrying the real ``.githooks``, ``scripts`` and ``config``, the two
private list files supplied through the gates' own documented overrides with
entries that match nothing, the files the license-spelling exemptions name,
and scaffolding pushes made with ``core.hooksPath`` emptied.  The deferral
framing and the ledger path are assembled from parts so this file does not
itself carry the phrases the deferral gate looks for.
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
_SANDBOX_DIRS = (".githooks", "scripts", "config")
REMOTE = "origin"

# Larger than a pipe holds (64 KiB on Linux), by a wide margin.
LONG = 3000                      # body lines of the long message: 177,000 bytes
BIG_FILE_LINES = 1500            # filler lines of the large recipe files
# Files in the long changed-file list: about 505 KB of names. The list comes
# from git, which can finish writing a list of about 100 KB before grep closes
# the pipe (measured: misread once in five at 1000 files, five times in five
# at 5000), so the list is made large enough that the old reader always fails.
NAME_LIST_FILES = 5000

# The deferral gate's framing and its ledger document, assembled from parts.
_DEFERRAL = " ".join(["this", "part", "is", "de" + "ferred", "to", "v1" + ".1"])
_LEDGER = "docs/v1.1-" + "de" + "ferred-" + "followups.md"
_TRAILER = "Co-Authored-By: Gate Test <gate-test@example.invalid>"

# A pipe followed by a reader that can stop before its input ends.
_EARLY_READER = re.compile(r"""(?<!\|)\|(?!\|)\s*(?:
      grep(?:\s+-[A-Za-z]*[qlLm][A-Za-z0-9]*\b
            |\s+--(?:quiet|silent|max-count|files-with-matches|files-without-match)\b)
    | head\b
    | sed\s+(?:-[nEr]+\s+)*['"]?[^'"|]*?(?:^|[;{}\s'"/0-9$])q\b
    | awk\b[^|]*\bexit\b
)""", re.X)


def _long_body() -> str:
    return "\n".join(f"Body line {i:04d} of a long commit message, plain words only."
                     for i in range(1, LONG + 1))


def _filler(prefix: str) -> str:
    return "".join(f"{prefix} filler line {i:04d}, present only to make this file larger than a pipe holds.\n"
                   for i in range(1, BIG_FILE_LINES + 1))


def _logical_lines(text: str):
    """(first line number, text) per shell line, continuation lines joined."""
    buf, start = "", None
    for number, line in enumerate(text.splitlines(), 1):
        if start is None:
            start = number
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        yield start, buf + line
        buf, start = "", None


def _sandbox_parent():
    """Ordinary disk, never the temporary filesystem (see the sibling test)."""
    if os.access(REPO.parent, os.W_OK):
        return REPO.parent
    return Path.home()


class TheHookHasNoEarlyStoppingReader(unittest.TestCase):

    def test_no_pipeline_in_the_hook_ends_in_a_reader_that_stops_early(self):
        offenders = [
            f"line {number}: {line.strip()}"
            for number, line in _logical_lines(PRE_PUSH.read_text(encoding="utf-8"))
            if line.strip() and not line.strip().startswith("#")
            and _EARLY_READER.search(line)
        ]
        self.assertEqual(
            [], offenders,
            "a pipeline in .githooks/pre-push ends in a reader that can stop "
            "before its input ends; under set -euo pipefail a long input fails "
            "it (read from a process substitution, or take a first line with "
            "parameter expansion):\n" + "\n".join(offenders))


class _Sandbox(unittest.TestCase):
    """A small real repository, one real bare remote, the real hook."""

    @classmethod
    def setUpClass(cls) -> None:
        for required in (PRE_PUSH, LICENSE_GATE):
            if not required.is_file():
                raise unittest.SkipTest(f"{required} is absent")
        cls.tmp = Path(tempfile.mkdtemp(prefix=".pre-push-long-input-",
                                        dir=str(_sandbox_parent())))
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
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
        cls.env = cls._env()
        cls.bare = cls.tmp / "remote.git"
        cls._git(cls.tmp, "init", "-q", "--bare", "-b", "master", str(cls.bare))
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
        cls._git(cls.work, "commit", "-qn", "-m", "chore(test): the sandbox tree, seeded once")
        cls._git(cls.work, "remote", "add", REMOTE, str(cls.bare))
        cls._git(cls.work, "branch", "dev")
        cls._git(cls.work, "-c", "core.hooksPath=", "push", "-q", REMOTE, "master", "dev")
        cls._git(cls.work, "config", "core.hooksPath", ".githooks")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _copy_the_files_the_license_exemptions_name(cls) -> None:
        listing = subprocess.run(
            ["python3", str(LICENSE_GATE), "--list-exemptions"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        for rel in sorted(set(re.findall(r"^[a-z0-9-]+: (\S+) ", listing.stdout, re.M))):
            source = REPO / rel
            if not source.is_file():
                continue
            target = cls.work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)

    @classmethod
    def _env(cls) -> dict:
        """No real remote, identity or configuration is reachable from here."""
        env = dict(os.environ)
        env.update({
            "HOME": str(cls.home),
            "GIT_CONFIG_GLOBAL": str(cls.home / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "Gate Test",
            "GIT_AUTHOR_EMAIL": "gate-test@example.invalid",
            "GIT_COMMITTER_NAME": "Gate Test",
            "GIT_COMMITTER_EMAIL": "gate-test@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
            "IGOS_PUBLIC_LANGUAGE_DENYLIST": str(cls.denylist),
            "IGOS_PUBLIC_CONTENT_PATTERNS": str(cls.patterns),
        })
        env.pop("GIT_PRE_PUSH_ALLOW_REWRITE", None)
        env.pop("GIT_PRE_PUSH_SKIP_FETCH", None)
        return env

    @classmethod
    def _git(cls, cwd, *args, check=True):
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, check=check, env=getattr(cls, "env", None))

    def _push_commit(self, branch: str, files: dict, message: str):
        """One commit on a new branch off master, pushed through the hook."""
        self._git(self.work, "checkout", "-q", "master")
        self._git(self.work, "checkout", "-q", "-B", branch)
        for rel, text in files.items():
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        # Only the files named here: the sandbox's own post-checkout and
        # post-commit hooks write an untracked stamp that must stay untracked.
        self._git(self.work, "add", "--", *files)
        msgfile = self.tmp / "message.txt"
        msgfile.write_text(message, encoding="utf-8")
        self._git(self.work, "commit", "-qn", "-F", str(msgfile))
        head = self._git(self.work, "rev-parse", "HEAD").stdout.strip()
        result = subprocess.run(["git", "push", REMOTE, branch], cwd=str(self.work),
                                capture_output=True, text=True, env=self.env)
        return result, head

    def _remote_head(self, branch: str) -> str:
        out = self._git(self.tmp, "ls-remote", str(self.bare), f"refs/heads/{branch}").stdout
        return out.split("\t")[0] if out.strip() else ""

    @staticmethod
    def _hook_said(result) -> str:
        """The hook's own lines, never git's transport messages."""
        return "\n".join(line.strip() for line in (result.stdout + result.stderr).splitlines()
                         if line.strip().startswith("[pre-push]"))

    def assertPublished(self, result, head, branch):
        said = self._hook_said(result)
        self.assertEqual(result.returncode, 0,
                         f"the push was refused. The hook said:\n{said or '(nothing at all)'}")
        self.assertIn("all gates PASS", said,
                      f"the hook did not reach its end:\n{said or '(nothing at all)'}")
        self.assertEqual(self._remote_head(branch), head, "the commit did not arrive")

    def assertRefusedSaying(self, result, branch, reason):
        said = self._hook_said(result)
        self.assertNotEqual(result.returncode, 0,
                            f"the push was accepted. The hook said:\n{said or '(nothing at all)'}")
        self.assertIn(reason, said,
                      "the push was refused without the gate's reason; the hook "
                      f"said:\n{said or '(nothing at all)'}")
        self.assertEqual(self._remote_head(branch), "", "the refused commit reached the remote")


class ALongInputGetsTheAnswerAShortOneGets(_Sandbox):

    def test_a_long_message_is_published(self):
        """The conventional-commit gate used to end the hook with no reason."""
        branch = "feature/a-long-message"
        result, head = self._push_commit(
            branch, {"marker.txt": "a long message\n"},
            "chore(test): a commit whose message is longer than a pipe holds\n\n"
            + _long_body() + "\n")
        self.assertPublished(result, head, branch)

    def test_a_no_gate_line_early_in_a_long_message_is_honoured(self):
        branch = "feature/no-gate-early-in-a-long-message"
        result, head = self._push_commit(
            branch, {"bulk/generated.txt": "".join(f"row {i}\n" for i in range(60))},
            "chore(test): a generated table\n\n"
            "NO-GATE: generated rows, reviewed as a whole\n\n" + _long_body() + "\n")
        self.assertPublished(result, head, branch)

    def test_a_no_gate_line_in_a_short_message_is_honoured(self):
        """Control: the same exemption, a short message."""
        branch = "feature/no-gate-in-a-short-message"
        result, head = self._push_commit(
            branch, {"bulk/generated-short.txt": "".join(f"row {i}\n" for i in range(60))},
            "chore(test): a generated table\n\n"
            "NO-GATE: generated rows, reviewed as a whole\n")
        self.assertPublished(result, head, branch)

    def test_a_file_named_early_in_a_long_message_counts_as_named(self):
        branch = "feature/named-early-in-a-long-message"
        result, head = self._push_commit(
            branch, {"notes/scope.txt": "".join(f"note {i}\n" for i in range(60))},
            "docs(test): notes/scope.txt gains sixty notes\n\n"
            + _long_body() + "\n\n" + _TRAILER + "\n")
        self.assertPublished(result, head, branch)

    def test_a_file_not_named_in_a_short_message_is_still_refused(self):
        """Control: the scope gate still refuses what it always refused."""
        branch = "feature/not-named-in-a-short-message"
        result, _ = self._push_commit(
            branch, {"notes/unnamed.txt": "".join(f"note {i}\n" for i in range(60))},
            "docs(test): sixty notes\n\n" + _TRAILER + "\n")
        self.assertRefusedSaying(result, branch, "but message doesn't mention it")

    def test_a_deferral_early_in_a_long_message_is_refused_with_its_reason(self):
        """Refused before too, but by the hook ending at gate 5 with no reason."""
        branch = "feature/deferral-early-in-a-long-message"
        result, _ = self._push_commit(
            branch, {"marker.txt": "a deferral\n"},
            f"chore(test): a narrower change\n\n{_DEFERRAL}.\n\n" + _long_body() + "\n")
        self.assertRefusedSaying(result, branch, "deferral(s) without ledger entry")

    def test_a_deferral_in_a_short_message_is_refused_with_its_reason(self):
        """Control."""
        branch = "feature/deferral-in-a-short-message"
        result, _ = self._push_commit(
            branch, {"marker.txt": "a deferral, short\n"},
            f"chore(test): a narrower change\n\n{_DEFERRAL}.\n")
        self.assertRefusedSaying(result, branch, "deferral(s) without ledger entry")

    def test_a_trailer_early_in_a_long_message_is_found(self):
        branch = "feature/trailer-early-in-a-long-message"
        result, head = self._push_commit(
            branch, {"notes/thirty.txt": "".join(f"note {i}\n" for i in range(30))},
            "docs(test): thirty notes\n\n" + _TRAILER + "\n\n" + _long_body() + "\n")
        self.assertPublished(result, head, branch)

    def test_a_large_new_recipe_that_declares_verify_paths_is_published(self):
        branch = "feature/a-large-new-recipe"
        recipe = "packages/extra/longrecipe/package.yml"
        result, head = self._push_commit(
            branch,
            {recipe: ("name: longrecipe\nversion: \"1.0\"\nrelease: 1\n"
                      "verify_paths:\n  - usr/share/longrecipe/README\n" + _filler("#")),
             "CHANGELOG.md": "# Changelog\n\n- longrecipe joins the sandbox's package set.\n"},
            f"feat(test): {recipe}, a recipe larger than a pipe holds\n\n{_TRAILER}\n")
        self.assertPublished(result, head, branch)

    def test_a_large_recipe_whose_install_function_shadows_install_is_refused(self):
        """The fail-open case: this recipe used to be PUBLISHED."""
        branch = "feature/a-large-shadowing-recipe"
        script = "packages/extra/shadowing/build.sh"
        result, _ = self._push_commit(
            branch,
            {script: ("#!/bin/bash\ninstall() {\n"
                      "    install -d \"$DESTDIR/usr/share/shadowing\"\n}\n" + _filler("#"))},
            f"feat(test): {script}, a recipe larger than a pipe holds\n\n{_TRAILER}\n")
        self.assertRefusedSaying(result, branch, "install() phase function + bare 'install' call")

    def test_a_small_recipe_whose_install_function_shadows_install_is_refused(self):
        """Control."""
        branch = "feature/a-small-shadowing-recipe"
        script = "packages/extra/shadowing-small/build.sh"
        result, _ = self._push_commit(
            branch,
            {script: ("#!/bin/bash\ninstall() {\n"
                      "    install -d \"$DESTDIR/usr/share/shadowing\"\n}\n")},
            f"feat(test): {script}\n")
        self.assertRefusedSaying(result, branch, "install() phase function + bare 'install' call")

    def test_a_ledger_entry_early_in_a_long_file_list_registers_the_deferral(self):
        branch = "feature/ledger-early-in-a-long-file-list"
        files = {_LEDGER: "# The ledger\n\n- F-901: the sandbox's registered deferral.\n"}
        for i in range(1, NAME_LIST_FILES + 1):
            files[f"zz-names/a-directory-name-long-enough-to-matter/"
                  f"file-{i:04d}-with-a-long-name-so-the-list-outgrows-a-pipe.txt"] = "x\n"
        result, head = self._push_commit(
            branch, files,
            f"docs(test): a registered deferral\n\n{_DEFERRAL}, and the ledger "
            f"records it in this commit.\n\n{_TRAILER}\n")
        self.assertPublished(result, head, branch)


if __name__ == "__main__":
    unittest.main()

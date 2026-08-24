# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The privileged-dispatch diagnostic must report a MEASURED cause.

Background. `pkexec` returns exit code 127 for several distinct conditions: the
program it was asked to run could not be executed, the caller is not authorized,
an authorization could not be obtained, or an internal error occurred. One of
those conditions is "the runner is missing"; the others are not.

The released dispatcher mapped the whole 127 class to a single sentence that
names the one cause it never checks — it reports the runner as missing without
ever asking the filesystem whether it is there — and, on that branch, computes
pkexec's real stderr and then discards it. A user acting on that message
reinstalls a package that was never broken.

These tests pin the contract the diagnostic owes:

  1. Runner-path presence is MEASURED, not assumed. With identical subprocess
     results, a present runner and an absent runner must produce DIFFERENT
     messages — that difference is only possible if the code looked.
  2. A present runner is never reported as missing or as a misinstalled package.
  3. An absent runner IS reported as missing — the truthful case still works.
  4. The real stderr is never discarded: whatever pkexec said reaches the user.
  5. The exit code reaches the user, so a report can be acted on without
     guesswork.

Updated 2026-08-24 for the transient-unit dispatch. Starting the runner through
the user manager adds two components the dispatch now depends on — systemd-run
itself, and a running user manager — and each is MEASURED for the same reason
the runner path is: a new failure mode reported as an old one is the same
false-diagnostic class. The harness therefore controls all three environment
facts, so a test can put the machine in a chosen state without changing the
machine. The default for every test below is "everything present", which is what
keeps these cases about pkexec's own codes.

Nothing here executes systemd-run, pkexec, the runner, or any tool:
`subprocess.run` is replaced by a stub that returns canned results and records
nothing else, and the runtime directory is a temporary one so no real dispatch
state is written.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import tool_registry as tr
from intergen.tool_registry import ToolRegistry
from intergen.interfaces.types import ToolCall
from intergen.interfaces.provenance import Provenance


class _Completed:
    """Stand-in for subprocess.CompletedProcess — nothing is executed."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# The three real-world conditions that share exit code 127. Only the last one is
# a missing runner; the first two are the ones the field report was wrong about.
SETUID_REFUSED = "pkexec must be setuid root"
NOT_AUTHORIZED = "Error executing command as another user: Not authorized"
RUNNER_ABSENT = "/usr/bin/intergen-privileged-runner: No such file or directory"


def _call() -> ToolCall:
    return ToolCall(
        name="manage_packages",
        arguments={"action": "upgrade"},
        call_id="diagnostic-truth",
        source_of_request=list(Provenance)[0],
    )


def _dispatch(returncode, stdout, stderr, *, runner_present,
              systemd_run_present=True, manager_present=True):
    """Run the dispatcher against a canned result and a chosen environment.

    The three *_present flags are the environment facts the diagnostic
    MEASURES. They default to present so each test below isolates the one
    condition it is about.

    Rewritten 2026-08-24 to build REAL files rather than stub os.path.exists.
    The diagnostic now distinguishes absence from a traversal failure, and a
    regular executable from a directory, which a single patched boolean cannot
    express — a harness that cannot represent the states under test would let
    a wrong answer pass. Only `shutil.which` is still stubbed, because
    systemd-run's presence is genuinely a PATH question and nothing else.

    Returns ToolResult.content.
    """
    completed = _Completed(returncode, stdout, stderr)

    def _fake_which(name):
        if name == "systemd-run":
            return "/usr/bin/systemd-run" if systemd_run_present else None
        return f"/usr/bin/{name}"

    with tempfile.TemporaryDirectory(prefix="privdiag-") as runtime:
        runtime_path = Path(runtime)

        # The runner: a real 0755 regular file, or a path that really is absent.
        runner = runtime_path / "runner-present"
        if runner_present:
            runner.write_text("#!/bin/sh\nexit 0\n")
            runner.chmod(0o755)
        else:
            runner = runtime_path / "runner-absent"

        # The user manager: a real file at the socket path, or nothing there.
        if manager_present:
            (runtime_path / "systemd").mkdir(exist_ok=True)
            (runtime_path / "systemd" / "private").touch()

        with mock.patch.dict(
                os.environ, {"XDG_RUNTIME_DIR": runtime}, clear=False), \
                mock.patch.object(tr, "_PKEXEC_RUNNER_PATH", str(runner)), \
                mock.patch.object(tr.subprocess, "run", return_value=completed), \
                mock.patch.object(tr.shutil, "which", side_effect=_fake_which):
            result = ToolRegistry._dispatch_via_pkexec(
                _call(), "manage_packages", {"action": "upgrade"},
                "token-placeholder",
            )
    return result.content


class PrivilegedDiagnosticTruthTests(unittest.TestCase):

    def test_presence_is_measured_not_assumed(self):
        """Same exit code, same stderr, different filesystem: different message.

        This is the discriminator. A dispatcher that never looks at the path
        cannot tell these two apart, so it cannot pass this test by wording.
        """
        present = _dispatch(127, "", SETUID_REFUSED, runner_present=True)
        absent = _dispatch(127, "", SETUID_REFUSED, runner_present=False)
        self.assertNotEqual(
            present, absent,
            "the diagnostic reports the same cause whether or not the runner "
            "exists, so runner presence is being asserted rather than measured",
        )

    def test_present_runner_is_never_called_missing(self):
        for stderr in (SETUID_REFUSED, NOT_AUTHORIZED, ""):
            with self.subTest(stderr=stderr or "(empty)"):
                content = _dispatch(127, "", stderr, runner_present=True)
                lowered = content.lower()
                self.assertNotIn("not present", lowered, content)
                self.assertNotIn("misinstalled", lowered, content)

    def test_absent_runner_is_reported_as_absent(self):
        content = _dispatch(127, "", RUNNER_ABSENT, runner_present=False)
        lowered = content.lower()
        self.assertIn(
            "not present", lowered,
            f"a genuinely absent runner must still be reported as absent: {content}",
        )

    def test_the_new_dependencies_are_measured_too(self):
        """The transition through the user manager added two components. Each
        is measured, and each measurably changes the message — the same
        discriminator as the runner path above, applied to the new surface."""
        both_present = _dispatch(1, "", "same stderr", runner_present=True)
        no_manager = _dispatch(1, "", "same stderr", runner_present=True,
                               manager_present=False)
        no_systemd_run = _dispatch(1, "", "same stderr", runner_present=True,
                                   systemd_run_present=False)
        self.assertNotEqual(both_present, no_manager)
        self.assertNotEqual(both_present, no_systemd_run)
        self.assertNotEqual(no_manager, no_systemd_run)

    def test_a_missing_user_manager_is_reported_as_the_measured_fact(self):
        """Rewritten 2026-08-24. This case used to assert the message said the
        installed package "is not a fault" — a verdict about a component the
        code never examined. What was actually established is that the user
        manager could not be reached, and that is what the message must say;
        the reader draws their own conclusion from a true statement."""
        content = _dispatch(1, "", "Failed to connect to user scope bus",
                            runner_present=True, manager_present=False)
        lowered = content.lower()
        self.assertNotIn("misinstalled", lowered, content)
        self.assertNotIn("not at fault", lowered, content)
        self.assertIn("no systemd user manager was reachable", lowered, content)

    def test_stderr_is_never_discarded(self):
        """Every non-zero branch surfaces what the dispatch actually said."""
        cases = [
            (126, "", "Error executing command as another user: Request dismissed"),
            (127, "", SETUID_REFUSED),
            (127, "", NOT_AUTHORIZED),
            (127, "", RUNNER_ABSENT),
            (5, "", "some other failure"),
        ]
        for rc, out, err in cases:
            for present in (True, False):
                with self.subTest(rc=rc, runner_present=present):
                    content = _dispatch(rc, out, err, runner_present=present)
                    self.assertIn(
                        err, content,
                        f"pkexec stderr was dropped on rc={rc}: {content!r}",
                    )

    def test_exit_code_reaches_the_user(self):
        for rc in (126, 127, 5):
            with self.subTest(rc=rc):
                content = _dispatch(rc, "", "something happened", runner_present=True)
                self.assertIn(str(rc), content, content)

    def test_success_path_is_unchanged(self):
        content = _dispatch(0, "upgraded 3 packages", "", runner_present=True)
        self.assertEqual(content, "upgraded 3 packages")

    def test_runner_message_still_wins_when_the_runner_spoke(self):
        """A refusal the runner itself reported stays the headline; the
        diagnostic augments it rather than replacing it."""
        runner_said = ("privileged_dispatch: dispatch token verification failed "
                       "(BadSignature): refusing dispatch.")
        content = _dispatch(1, runner_said, "", runner_present=True)
        self.assertIn(runner_said, content)


# ---------------------------------------------------------------------------
# The cause-assignment cases (added 2026-08-24 after an independent review).
#
# The review drove three filesystem states that are genuinely different — a
# directory, a regular file nothing can execute, and a healthy executable whose
# PROGRAM returned 127 — and got the same "the installed package is not at
# fault / pkexec stopped before reaching it" headline from all three. The
# diagnostic was reading one boolean, os.path.exists(), and speaking as though
# it had read four facts.
#
# pkexec(1) propagates the status of a program it successfully executed, and the
# runner ends in `exec python3 ...`. So 126 and 127 can be RAISED BY THE CHILD.
# "126 means the prompt was dismissed" and "127 with a present runner means
# pkexec never got there" are therefore inferences, not measurements.
#
# These tests use REAL files in a temporary directory rather than a stubbed
# os.path.exists: the point is that the probe reports what a filesystem actually
# says, and a stub that returns whatever the test wants cannot show that.
# ---------------------------------------------------------------------------

_FORBIDDEN_CAUSE_CLAIMS = (
    "dismissed or denied",
    "not at fault",
    "stopped before reaching",
    "misinstalled",
)


def _dispatch_with_real_runner_path(returncode, stderr, runner_path,
                                    *, stdout=""):
    """Dispatch against a canned result but a REAL runner path on disk."""
    completed = _Completed(returncode, stdout, stderr)
    with tempfile.TemporaryDirectory(prefix="privdiag-real-") as runtime:
        os.makedirs(os.path.join(runtime, "systemd"), exist_ok=True)
        # A real user-manager socket stand-in, so the manager probe is not the
        # thing under test here.
        open(os.path.join(runtime, "systemd", "private"), "w").close()
        with mock.patch.dict(
                os.environ, {"XDG_RUNTIME_DIR": runtime}, clear=False), \
                mock.patch.object(tr, "_PKEXEC_RUNNER_PATH", str(runner_path)), \
                mock.patch.object(tr.subprocess, "run", return_value=completed):
            result = ToolRegistry._dispatch_via_pkexec(
                _call(), "manage_packages", {"action": "upgrade"},
                "token-placeholder",
            )
    return result.content


class DiagnosticAssignsNoUnmeasuredCauseTests(unittest.TestCase):
    """No branch may state a cause the code did not establish."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="privdiag-states-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _states(self):
        """Return (label, path) for each distinguishable filesystem state."""
        absent = self.root / "absent-runner"

        directory = self.root / "directory-runner"
        directory.mkdir()

        non_exec = self.root / "non-executable-runner"
        non_exec.write_text("#!/bin/sh\nexit 0\n")
        non_exec.chmod(0o644)

        healthy = self.root / "healthy-runner"
        healthy.write_text("#!/bin/sh\nexit 0\n")
        healthy.chmod(0o755)

        broken_interp = self.root / "broken-interpreter-runner"
        broken_interp.write_text("#!/nonexistent/interpreter\nexit 0\n")
        broken_interp.chmod(0o755)

        unreadable_dir = self.root / "unreadable"
        unreadable_dir.mkdir()
        hidden = unreadable_dir / "runner"
        hidden.write_text("#!/bin/sh\nexit 0\n")
        hidden.chmod(0o755)
        unreadable_dir.chmod(0o000)
        self.addCleanup(unreadable_dir.chmod, 0o700)

        return [
            ("absent", absent),
            ("directory", directory),
            ("regular-non-executable", non_exec),
            ("regular-executable", healthy),
            ("broken-interpreter", broken_interp),
            ("permission-denied-on-path", hidden),
        ]

    def test_no_state_produces_an_unmeasured_cause_claim(self):
        """Not one of the six states may yield an innocence or reachability
        verdict, at either exit code the review named."""
        for label, path in self._states():
            for rc in (126, 127):
                with self.subTest(state=label, rc=rc):
                    content = _dispatch_with_real_runner_path(
                        rc, "some stderr from the boundary", path)
                    lowered = content.lower()
                    for claim in _FORBIDDEN_CAUSE_CLAIMS:
                        self.assertNotIn(
                            claim, lowered,
                            f"state={label} rc={rc}: the diagnostic states a "
                            f"cause it did not measure: {content!r}",
                        )

    def test_distinguishable_states_are_reported_distinguishably(self):
        """A directory, a non-executable file and a healthy executable are
        three different findings and must not share one message."""
        seen = {}
        for label, path in self._states():
            seen[label] = _dispatch_with_real_runner_path(
                127, "identical stderr", path)
        for a, b in (
            ("directory", "regular-non-executable"),
            ("directory", "regular-executable"),
            ("regular-non-executable", "regular-executable"),
            ("absent", "regular-executable"),
        ):
            with self.subTest(pair=f"{a} vs {b}"):
                self.assertNotEqual(
                    seen[a], seen[b],
                    f"{a} and {b} produce the same message, so the diagnostic "
                    f"is not distinguishing states it claims to have checked",
                )

    def test_a_child_returned_126_is_not_called_a_dismissed_prompt(self):
        """The runner ends in exec; 126 can come from the child."""
        healthy = dict(self._states())["regular-executable"]
        content = _dispatch_with_real_runner_path(
            126, "", healthy)
        self.assertNotIn("dismissed", content.lower(), content)

    def test_permission_denied_is_not_reported_as_absence(self):
        """os.path.exists() answers False for EACCES; absence is a different
        finding and must not be asserted from a traversal failure."""
        hidden = dict(self._states())["permission-denied-on-path"]
        content = _dispatch_with_real_runner_path(127, "", hidden)
        lowered = content.lower()
        self.assertNotIn(
            "absent", lowered,
            f"a path we could not traverse was reported as absent: {content!r}")
        self.assertNotIn("missing", lowered, content)

    def test_the_exit_code_and_stderr_still_reach_the_user_in_every_state(self):
        for label, path in self._states():
            with self.subTest(state=label):
                content = _dispatch_with_real_runner_path(
                    127, "the boundary said this", path)
                self.assertIn("127", content, content)
                self.assertIn("the boundary said this", content, content)


if __name__ == "__main__":
    unittest.main()

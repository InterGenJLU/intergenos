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

import contextlib
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


def _manager_routing_run(dispatch_result, manager_answer):
    """A `subprocess.run` stand-in that tells the two calls apart.

    The diagnostic now runs a second command — it asks the user manager about
    itself — and a stub that answered every call with the dispatch's canned
    result would feed the dispatch's stdout to the manager probe. The two
    questions would then be indistinguishable in the harness, which is exactly
    the confusion the code change exists to end. Route on the argv.
    """

    def _run(argv, *args, **kwargs):
        if len(argv) >= 3 and tuple(argv[1:3]) == ("--user", "is-system-running"):
            if manager_answer is None:
                raise FileNotFoundError(2, "No such file or directory", argv[0])
            return _Completed(0 if manager_answer == "running" else 1,
                              manager_answer + "\n", "")
        return dispatch_result

    return _run


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
    a wrong answer pass.

    Amended the same day for the manager probe: `manager_present` is now
    expressed as the manager's OWN answer, because that is what the code reads.
    systemd-run's presence is a real file on a real path here for the same
    reason the runner's is.

    Returns ToolResult.content.
    """
    completed = _Completed(returncode, stdout, stderr)

    with tempfile.TemporaryDirectory(prefix="privdiag-") as runtime:
        runtime_path = Path(runtime)

        # The runner: a real 0755 regular file, or a path that really is absent.
        runner = runtime_path / "runner-present"
        if runner_present:
            runner.write_text("#!/bin/sh\nexit 0\n")
            runner.chmod(0o755)
        else:
            runner = runtime_path / "runner-absent"

        # systemd-run: likewise a real path, present or absent.
        systemd_run = runtime_path / "systemd-run-present"
        if systemd_run_present:
            systemd_run.write_text("#!/bin/sh\nexit 0\n")
            systemd_run.chmod(0o755)
        else:
            systemd_run = runtime_path / "systemd-run-absent"

        with mock.patch.dict(
                os.environ, {"XDG_RUNTIME_DIR": runtime}, clear=False), \
                mock.patch.object(tr, "_PKEXEC_RUNNER_PATH", str(runner)), \
                mock.patch.object(tr, "_SYSTEMD_RUN", str(systemd_run)), \
                mock.patch.object(
                    tr.subprocess, "run",
                    side_effect=_manager_routing_run(
                        completed,
                        "running" if manager_present else "offline",
                    )):
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
        """Rewritten 2026-08-24, twice, and the second rewrite is the point.

        It first asserted the message said the installed package "is not a
        fault" — a verdict about a component the code never examined. It then
        asserted the message said the manager "was not reachable", which was a
        true-sounding sentence resting on a path check that a second review
        measured disagreeing with the mechanism.

        What is established now is narrower and actually established: the
        manager was ASKED, and it answered "offline". So the sentence quotes the
        word rather than paraphrasing it, and the reader draws their own
        conclusion from something the code really has.
        """
        content = _dispatch(1, "", "Failed to connect to user scope bus",
                            runner_present=True, manager_present=False)
        lowered = content.lower()
        self.assertNotIn("misinstalled", lowered, content)
        self.assertNotIn("not at fault", lowered, content)
        self.assertIn("offline", lowered, content)
        self.assertIn("is-system-running", lowered, content)

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
        # The manager answers "running", so it is not the thing under test here
        # and the runner's own state is what the message has to turn on.
        with mock.patch.dict(
                os.environ, {"XDG_RUNTIME_DIR": runtime}, clear=False), \
                mock.patch.object(tr, "_PKEXEC_RUNNER_PATH", str(runner_path)), \
                mock.patch.object(
                    tr.subprocess, "run",
                    side_effect=_manager_routing_run(completed, "running")):
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


# --- The second review's F-01 and F-06 ---------------------------------------
#
# F-01. The diagnostic decided whether a systemd user manager was running by
# asking whether $XDG_RUNTIME_DIR/systemd/private EXISTS, and printed "(checked)"
# beside the answer. An independent review measured that probe reading False
# while `systemd-run --user … /bin/true` succeeded, so the sentence carrying
# "(checked)" could be false at the moment it was printed. That socket is also a
# systemd-internal detail, not a supported presence interface: its absence is
# evidence about a path, and the sentence was about a manager.
#
# The contract these tests pin: the manager is measured by ASKING THE MANAGER,
# and the answer reported is the manager's own word. Three outcomes are
# distinguishable — it answered that it is running, it answered "offline", or
# the question could not be put at all — and only the middle one may carry a
# causal sentence. "I could not tell" must never be reported as "it is not
# running", which is the whole class of defect this file exists to keep out.
#
# F-06. The privileged entry path resolved `systemd-run` through PATH while the
# runner and the interpreter beyond it are both absolute.

_MANAGER_PROBE_HEAD = ("--user", "is-system-running")


def _dispatch_with_manager(manager_answer, *, socket_present, returncode=127,
                           stderr="Error executing command as another user: "
                                  "Not authorized",
                           runtime=None):
    """Dispatch with the manager's OWN answer chosen, and the socket path
    chosen independently of it.

    The two are deliberately separable here, because the defect under test is
    exactly that the code treated one as evidence for the other.

    `manager_answer` is the word `systemctl --user is-system-running` prints, or
    None to mean the probe could not run at all (the command is missing).

    `runtime` lets a caller reuse ONE directory across two dispatches. A test
    that compares two messages has to hold every other path fixed, or the
    temporary directory's own random name is the difference it measures — which
    is the mistake this parameter exists to stop.
    """
    completed = _Completed(returncode, "", stderr)

    def _run(argv, *args, **kwargs):
        # Route on the command being run, so the dispatch result and the
        # manager probe cannot be confused for one another.
        if len(argv) >= 3 and tuple(argv[1:3]) == _MANAGER_PROBE_HEAD:
            if manager_answer is None:
                raise FileNotFoundError(2, "No such file or directory", argv[0])
            return _Completed(0 if manager_answer == "running" else 1,
                              manager_answer + "\n", "")
        return completed

    with contextlib.ExitStack() as stack:
        if runtime is None:
            runtime = stack.enter_context(
                tempfile.TemporaryDirectory(prefix="privdiag-manager-"))
        runner = Path(runtime) / "runner"
        if not runner.exists():
            runner.write_text("#!/bin/sh\nexit 0\n")
            runner.chmod(0o755)
        socket = Path(runtime) / "systemd" / "private"
        if socket_present:
            socket.parent.mkdir(exist_ok=True)
            socket.touch()
        elif socket.exists():
            socket.unlink()
        stack.enter_context(mock.patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": str(runtime)}, clear=False))
        stack.enter_context(
            mock.patch.object(tr, "_PKEXEC_RUNNER_PATH", str(runner)))
        stack.enter_context(
            mock.patch.object(tr.subprocess, "run", side_effect=_run))
        result = ToolRegistry._dispatch_via_pkexec(
            _call(), "manage_packages", {"action": "upgrade"},
            "token-placeholder",
        )
    return result.content


class TheUserManagerIsMeasuredByAskingItTests(unittest.TestCase):
    """F-01 — the manager's state is the manager's answer, not a path's."""

    def test_a_running_manager_is_not_called_unreachable_because_a_socket_is_gone(self):
        """The measured case from the review: the socket path is absent and the
        manager is running. The old probe called that "not reachable"."""
        content = _dispatch_with_manager("running", socket_present=False)
        lowered = content.lower()
        self.assertNotIn("no systemd user manager", lowered, content)
        self.assertNotIn("not reachable", lowered, content)

    def test_the_manager_s_own_word_is_what_gets_reported(self):
        """A degraded manager is running. Reporting the word it gave is the
        difference between quoting a measurement and paraphrasing one."""
        content = _dispatch_with_manager("degraded", socket_present=False)
        self.assertIn("degraded", content.lower(), content)

    def test_an_offline_manager_is_reported_as_not_running(self):
        """The truthful negative still works, and it comes from the manager."""
        content = _dispatch_with_manager("offline", socket_present=True)
        self.assertIn("offline", content.lower(), content)

    def test_a_probe_that_could_not_run_states_no_manager_conclusion(self):
        """Undetermined is its own answer. It must not be reported as absence,
        and it must not carry a cause."""
        content = _dispatch_with_manager(None, socket_present=False)
        lowered = content.lower()
        self.assertNotIn("no systemd user manager was reachable", lowered,
                         content)
        for claim in _FORBIDDEN_CAUSE_CLAIMS:
            self.assertNotIn(claim, lowered, content)

    def test_an_unknown_answer_is_not_turned_into_absence(self):
        content = _dispatch_with_manager("unknown", socket_present=False)
        self.assertNotIn("no systemd user manager was reachable",
                         content.lower(), content)

    def test_the_socket_path_is_no_longer_consulted_at_all(self):
        """With the manager answering, the socket's presence must make no
        difference to the message. If it does, something still reads it.

        ONE runtime directory serves both dispatches, so the only thing that
        differs between them is the socket. Two directories would have differed
        by their random names as well, and the comparison would have proved
        nothing.
        """
        with tempfile.TemporaryDirectory(prefix="privdiag-socket-") as runtime:
            with_socket = _dispatch_with_manager(
                "running", socket_present=True, runtime=runtime)
            without_socket = _dispatch_with_manager(
                "running", socket_present=False, runtime=runtime)
        self.assertEqual(with_socket, without_socket)


class TheSystemdRunPathIsAbsoluteTests(unittest.TestCase):
    """F-06 — the privileged entry path does not resolve a program by PATH."""

    def test_the_configured_systemd_run_is_an_absolute_path(self):
        self.assertTrue(
            os.path.isabs(tr._SYSTEMD_RUN),
            f"the privileged dispatch resolves {tr._SYSTEMD_RUN!r} through "
            f"PATH; the runner and the interpreter beyond it are both absolute "
            f"and this one is the way in",
        )

    def test_the_argv_the_dispatch_builds_starts_with_that_absolute_path(self):
        """The constant being absolute is not the same as the argv using it."""
        seen = {}

        def _run(argv, *args, **kwargs):
            seen.setdefault("argv", list(argv))
            return _Completed(0, "done", "")

        with tempfile.TemporaryDirectory(prefix="privdiag-abs-") as runtime:
            with mock.patch.dict(
                    os.environ, {"XDG_RUNTIME_DIR": runtime}, clear=False), \
                    mock.patch.object(tr.subprocess, "run", side_effect=_run):
                ToolRegistry._dispatch_via_pkexec(
                    _call(), "manage_packages", {"action": "upgrade"},
                    "token-placeholder",
                )
        self.assertIn("argv", seen, "the dispatch built no command at all")
        self.assertTrue(
            os.path.isabs(seen["argv"][0]),
            f"argv[0] is {seen['argv'][0]!r}, resolved through PATH",
        )

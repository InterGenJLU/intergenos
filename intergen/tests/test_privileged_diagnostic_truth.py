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

    Returns ToolResult.content.
    """
    completed = _Completed(returncode, stdout, stderr)

    def _fake_exists(path):
        if path == tr._PKEXEC_RUNNER_PATH:
            return runner_present
        if path.endswith(os.path.join("systemd", "private")):
            return manager_present
        return False

    def _fake_which(name):
        if name == "systemd-run":
            return "/usr/bin/systemd-run" if systemd_run_present else None
        return f"/usr/bin/{name}"

    with tempfile.TemporaryDirectory(prefix="privdiag-") as runtime:
        with mock.patch.dict(
                os.environ, {"XDG_RUNTIME_DIR": runtime}, clear=False), \
                mock.patch.object(tr.subprocess, "run", return_value=completed), \
                mock.patch.object(tr.shutil, "which", side_effect=_fake_which), \
                mock.patch.object(tr.os.path, "exists", side_effect=_fake_exists):
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
                self.assertNotIn("not found", lowered, content)
                self.assertNotIn("misinstalled", lowered, content)

    def test_absent_runner_is_reported_as_absent(self):
        content = _dispatch(127, "", RUNNER_ABSENT, runner_present=False)
        lowered = content.lower()
        self.assertTrue(
            "not found" in lowered or "missing" in lowered,
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

    def test_a_missing_user_manager_does_not_blame_the_installed_package(self):
        content = _dispatch(1, "", "Failed to connect to user scope bus",
                            runner_present=True, manager_present=False)
        lowered = content.lower()
        self.assertNotIn("misinstalled", lowered, content)
        self.assertIn("not a fault in the installed package", lowered, content)

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


if __name__ == "__main__":
    unittest.main()

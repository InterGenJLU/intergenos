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

Nothing here executes pkexec, the runner, or any tool: `subprocess.run` is
replaced by a stub that returns canned results and records nothing else.
"""

from __future__ import annotations

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


def _dispatch(returncode, stdout, stderr, *, runner_present):
    """Run the released dispatcher against a canned result and a chosen
    filesystem state for the runner path. Returns ToolResult.content."""
    completed = _Completed(returncode, stdout, stderr)

    def _fake_exists(path):
        if path == tr._PKEXEC_RUNNER_PATH:
            return runner_present
        return False

    with mock.patch.object(tr.subprocess, "run", return_value=completed), \
            mock.patch("os.path.exists", side_effect=_fake_exists), \
            mock.patch("os.path.isfile", side_effect=_fake_exists):
        result = ToolRegistry._dispatch_via_pkexec(
            _call(), "manage_packages", {"action": "upgrade"}, "token-placeholder",
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

    def test_stderr_is_never_discarded(self):
        """Every non-zero branch surfaces what pkexec actually said."""
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

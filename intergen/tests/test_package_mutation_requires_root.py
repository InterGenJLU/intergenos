# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A package MUTATION may only be attempted by a process that is already root.

WHY THIS TEST EXISTS. The package-management tool used to build
["pkexec", "pkm", <action>, <package>] for every state-changing action. That
second privilege transition was removed on 2026-08-24: by the time the builder
runs, the dispatcher has already carried the call across the boundary once,
verified the human-approval token, and is executing as root. One approval, one
crossing.

Removing the transition leaves an assumption where a construction used to be.
The code now says, in a comment, "this code is running as root inside that
dispatcher" — and a comment is not a check. If any future path reaches the
mutating branch without having crossed the boundary, the old code would at
least have tried to elevate; the new code would quietly run an unprivileged
`pkm install` and report whatever confusion came back. An independent review
named exactly this: removing the nested transition needs a root invariant to
replace it.

So the invariant is made explicit and testable:

  * a mutating action refuses unless os.geteuid() == 0, and refuses BEFORE
    attempting any command;
  * a mutating action, when it does run, invokes pkm by ABSOLUTE path — a root
    process resolving a bare program name through PATH is a different and worse
    thing than a root process naming the binary it means;
  * read-only actions are untouched: they classify AUTO, run as the user, and
    must keep working exactly as before.

THE REFUSAL IS ASSERTED ON THE ATTEMPT, NOT THE OUTCOME. A test that only
checks `success is False` passes just as happily when the guard is deleted and
the command fails for some unrelated reason. Every refusal case below asserts
that subprocess.run was never called — the guard has to be the reason.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.tools.manage_packages import ManagePackagesTool

MUTATING = ("install", "remove", "uninstall", "update", "upgrade")
READ_ONLY = ("list", "search", "info", "verify")

#: Arguments that make each action well-formed, so a refusal under test is the
#: root guard and never a missing-parameter rejection.
_ARGS = {
    "install": {"package": "hello"},
    "remove": {"package": "hello"},
    "uninstall": {"package": "hello"},
    "update": {},
    "upgrade": {},
    "list": {},
    "search": {"query": "hello"},
    "info": {"package": "hello"},
    "verify": {"package": "hello"},
}


class _Completed:
    def __init__(self, returncode=0, stdout="ok", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run(action, *, euid):
    """Execute `action` with a chosen effective uid. Nothing is ever executed.

    Returns (ToolResult, the subprocess.run mock) so a caller can assert on
    what was ATTEMPTED as well as what came back.
    """
    tool = ManagePackagesTool()
    args = {"action": action, **_ARGS[action]}
    with mock.patch("intergen.tools.manage_packages.shutil.which",
                    return_value="/usr/bin/pkm"), \
            mock.patch("intergen.tools.manage_packages.os.geteuid",
                       return_value=euid), \
            mock.patch("intergen.tools.manage_packages.subprocess.run",
                       return_value=_Completed()) as runner:
        result = tool.execute(args)
    return result, runner


class MutationRefusesWhenNotRoot(unittest.TestCase):

    def test_every_mutating_action_refuses_without_root(self):
        for action in MUTATING:
            with self.subTest(action=action):
                result, runner = _run(action, euid=1000)
                self.assertFalse(result.success, result.content)
                runner.assert_not_called()

    def test_the_refusal_says_what_was_measured(self):
        result, _ = _run("install", euid=1000)
        self.assertIn("root", result.content.lower(), result.content)

    def test_read_only_actions_are_unaffected_by_the_guard(self):
        for action in READ_ONLY:
            with self.subTest(action=action):
                result, runner = _run(action, euid=1000)
                self.assertTrue(result.success, result.content)
                runner.assert_called_once()


class MutationUsesAnAbsolutePkmPath(unittest.TestCase):

    def test_a_root_mutation_invokes_pkm_by_absolute_path(self):
        for action in MUTATING:
            with self.subTest(action=action):
                result, runner = _run(action, euid=0)
                runner.assert_called_once()
                argv = runner.call_args.args[0]
                self.assertTrue(
                    argv[0].startswith("/"),
                    f"a root process resolved {argv[0]!r} through PATH; name "
                    f"the binary absolutely: {argv}",
                )

    def test_no_mutating_argv_builds_a_second_transition(self):
        """The removed pkexec must not come back by any route."""
        for action in MUTATING:
            with self.subTest(action=action):
                _, runner = _run(action, euid=0)
                argv = runner.call_args.args[0]
                for word in argv:
                    self.assertNotIn("pkexec", word, argv)
                    self.assertNotIn("sudo", word, argv)


class TheGuardIsTheReasonForTheRefusal(unittest.TestCase):
    """Instrument control.

    If these refusal tests could pass with the guard absent, they would be
    measuring nothing. This case pins the discriminator: the SAME action, the
    same well-formed arguments, and the same stubbed command runner, differing
    only in effective uid, must differ in whether a command was attempted at
    all.
    """

    def test_effective_uid_is_the_only_difference_that_decides_it(self):
        _, refused = _run("install", euid=1000)
        _, allowed = _run("install", euid=0)
        refused.assert_not_called()
        allowed.assert_called_once()


if __name__ == "__main__":
    unittest.main()

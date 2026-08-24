# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The privileged runner must start as a transient unit, carrying no secrets on argv.

Background — the defect. The assistant runs as a systemd USER service whose unit
sets `NoNewPrivileges=yes`. That flag is inherited by every process the daemon
starts and cannot be cleared once set. `pkexec` is setuid-root: under that flag
the kernel declines to apply the setuid bit, so pkexec starts as the ordinary
user, sees that its own effective uid is not root, and refuses with exit 127 and
`pkexec must be setuid root` — BEFORE PolicyKit is contacted at all. No install
of R001.1 can perform any privileged action through the assistant.

The correction, adopted 2026-08-24. The daemon stops starting pkexec as its own
child. It asks its own systemd USER MANAGER to start a short-lived unit that
runs the same pkexec invocation. The manager is not running under the daemon's
flag, so the unit it starts begins from the manager's own context and the
restriction is simply not present in the new child. The daemon's hardening is
not touched, and the narrow PolicyKit action that gates the runner is unchanged.

Measured on this machine before the design was written: a caller carrying the
flag asked the user manager to run a probe and the probe reported
`NoNewPrivs: 0`; the same call from a caller without the flag also reported 0;
the caller's own context at that moment reported 1; and the same unit shape
carrying `NoNewPrivileges=yes` reproduced the `pkexec must be setuid root`
refusal exactly.

What these tests pin:

  1. THE DISPATCH GOES THROUGH THE USER MANAGER. The command starts with
     `systemd-run --user`, waits for the unit, and collects it.
  2. THE UNIT DOES NOT RE-IMPOSE THE FLAG. Nothing in the invocation sets
     NoNewPrivileges — re-imposing it would reproduce the defect exactly.
  3. THE COMMAND LINE CARRIES NO PROTECTED VALUE. Neither the approval token
     nor the serialized arguments may appear anywhere in the argv. Only the
     request file's path does. This is the /proc exposure closed.
  4. THE REQUEST IS WRITTEN BEFORE, AND DISCARDED AFTER, ON EVERY PATH —
     success, non-zero exit, and an exception raised out of the call.
  5. THE DIAGNOSTIC MEASURES THE NEW DEPENDENCY. Starting through the user
     manager means dispatch now depends on that manager being reachable. When
     it is not, the failure says so, having CHECKED — it does not blame the
     runner, which is exactly the false-diagnostic class the previous commit
     pair corrected for pkexec's own 127.

Nothing here executes systemd-run, pkexec, the runner, or any tool:
`subprocess.run` is replaced by a recorder that returns canned results. The
runtime directory is a temporary one, so no real dispatch state is written.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from intergen import privileged_request as pr
from intergen import tool_registry as tr
from intergen.tool_registry import ToolRegistry
from intergen.interfaces.types import ToolCall
from intergen.interfaces.provenance import Provenance


TOKEN = "v1.approval-token-that-must-never-appear-on-a-command-line"
ARGS = {"action": "install", "package": "a-package-name-that-is-distinctive"}


class _Completed:
    """Stand-in for subprocess.CompletedProcess — nothing is executed."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _call() -> ToolCall:
    return ToolCall(
        name="manage_packages",
        arguments=dict(ARGS),
        call_id="transient-unit-dispatch",
        source_of_request=list(Provenance)[0],
    )


class _DispatchTestCase(unittest.TestCase):
    """Base: a throwaway runtime directory, and subprocess replaced by a recorder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="privdispatch-")
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": self._tmp.name}, clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.recorded_argv: list[list[str]] = []

    def _dispatch(self, completed=None, *, raises=None,
                  runner_present=True, systemd_run_present=True,
                  manager_present=True):
        """Drive the dispatcher against a canned subprocess result.

        The three *_present flags control what the dispatcher MEASURES about
        its environment, so a test can put the machine in a chosen state
        without changing the machine.
        """
        if completed is None:
            completed = _Completed(0, "done", "")

        def _fake_run(argv, **kwargs):
            self.recorded_argv.append(list(argv))
            if raises is not None:
                raise raises
            return completed

        def _fake_which(name):
            if name == "systemd-run":
                return "/usr/bin/systemd-run" if systemd_run_present else None
            return f"/usr/bin/{name}"

        real_exists = os.path.exists

        def _fake_exists(path):
            if path == tr._PKEXEC_RUNNER_PATH:
                return runner_present
            if path.endswith(os.path.join("systemd", "private")):
                return manager_present
            return real_exists(path)

        with mock.patch.object(tr.subprocess, "run", side_effect=_fake_run), \
                mock.patch.object(tr.shutil, "which", side_effect=_fake_which), \
                mock.patch.object(tr.os.path, "exists", side_effect=_fake_exists):
            return ToolRegistry._dispatch_via_pkexec(
                _call(), "manage_packages", dict(ARGS), TOKEN,
            )

    @property
    def argv(self) -> list[str]:
        self.assertEqual(
            len(self.recorded_argv), 1,
            f"expected exactly one subprocess invocation, got "
            f"{len(self.recorded_argv)}",
        )
        return self.recorded_argv[0]

    def _requests_left_behind(self) -> list[str]:
        directory = pr.request_dir()
        if not os.path.isdir(directory):
            return []
        return [f for f in os.listdir(directory)
                if f.startswith(pr.REQUEST_PREFIX)]


class GoesThroughTheUserManagerTests(_DispatchTestCase):

    def test_invocation_starts_with_systemd_run_user(self):
        self._dispatch()
        self.assertEqual(
            self.argv[:2], ["systemd-run", "--user"],
            f"the runner is not being started through the user manager: "
            f"{self.argv}",
        )

    def test_invocation_waits_for_the_unit(self):
        self._dispatch()
        self.assertIn(
            "--wait", self.argv,
            "without --wait the dispatcher cannot know whether the privileged "
            "action ran, let alone what it returned",
        )

    def test_invocation_collects_the_unit(self):
        self._dispatch()
        self.assertIn(
            "--collect", self.argv,
            "without --collect a failed dispatch leaves a failed unit loaded in "
            "the user manager for every attempt",
        )

    def test_invocation_pipes_so_output_can_be_read(self):
        self._dispatch()
        self.assertIn(
            "--pipe", self.argv,
            "without --pipe the runner's stdout never reaches the dispatcher, "
            "so a refusal it explained would be lost",
        )

    def test_pkexec_and_the_runner_are_still_the_command(self):
        """The PolicyKit boundary is unchanged: the unit still runs the same
        pkexec invocation against the same narrow action's exec.path."""
        self._dispatch()
        self.assertIn("pkexec", self.argv)
        self.assertIn(tr._PKEXEC_RUNNER_PATH, self.argv)
        pkexec_at = self.argv.index("pkexec")
        self.assertEqual(
            self.argv[pkexec_at + 1], tr._PKEXEC_RUNNER_PATH,
            "pkexec must invoke the runner directly — the PolicyKit action is "
            "bound to that exact exec.path",
        )

    def test_double_dash_precedes_the_command(self):
        """`--` before the command, so a request path that begins with a dash
        can never be read as a systemd-run option."""
        self._dispatch()
        self.assertIn("--", self.argv)
        self.assertLess(
            self.argv.index("--"), self.argv.index("pkexec"),
            "the command must follow `--`, so nothing in it can be parsed as a "
            "systemd-run option",
        )


class TheFlagIsNotReimposedTests(_DispatchTestCase):

    def test_no_new_privileges_is_never_set_on_the_transient_unit(self):
        """Re-imposing the flag would reproduce the defect exactly.

        This is the single assertion that most directly pins the correction:
        the whole point of going through the user manager is that the new child
        does NOT carry the daemon's no_new_privs.
        """
        self._dispatch()
        joined = " ".join(self.argv)
        self.assertNotIn(
            "NoNewPrivileges", joined,
            f"the transient unit sets NoNewPrivileges, which reproduces the "
            f"defect this change exists to correct: {self.argv}",
        )

    def test_no_hardening_property_is_imposed_on_the_unit(self):
        """The unit is deliberately plain. Any -p/--property here would be a
        policy decision made in a command builder rather than in a unit file,
        which is where this project keeps them."""
        self._dispatch()
        for arg in self.argv:
            self.assertFalse(
                arg.startswith("--property") or arg == "-p",
                f"unexpected unit property on the dispatch: {arg}",
            )


class NoProtectedValueOnTheCommandLineTests(_DispatchTestCase):
    """The /proc exposure. A command line is world-readable for the life of the
    process, and this image carries no hidepid."""

    def test_the_token_never_appears_on_argv(self):
        self._dispatch()
        for arg in self.argv:
            self.assertNotIn(
                TOKEN, arg,
                f"the approval token is on the command line, readable by any "
                f"local account: {self.argv}",
            )

    def test_the_serialized_arguments_never_appear_on_argv(self):
        self._dispatch()
        serialized = json.dumps(ARGS)
        for arg in self.argv:
            self.assertNotEqual(arg, serialized)
        joined = " ".join(self.argv)
        self.assertNotIn(
            "a-package-name-that-is-distinctive", joined,
            f"the tool arguments are on the command line, so what the user "
            f"asked for is readable by any local account: {self.argv}",
        )

    def test_a_request_path_is_what_is_passed_instead(self):
        self._dispatch()
        runner_at = self.argv.index(tr._PKEXEC_RUNNER_PATH)
        passed = self.argv[runner_at + 1:]
        self.assertEqual(
            len(passed), 1,
            f"the runner should receive exactly one argument, the request "
            f"path; got {passed}",
        )
        self.assertTrue(
            os.path.basename(passed[0]).startswith(pr.REQUEST_PREFIX),
            f"the runner's single argument is not a request path: {passed[0]}",
        )

    def test_the_request_file_holds_what_left_the_command_line(self):
        """Proves the values were MOVED, not dropped: the token and arguments
        are in the file, which is why they are not on argv."""
        captured = {}

        def _capture_run(argv, **kwargs):
            self.recorded_argv.append(list(argv))
            path = argv[-1]
            with open(path, encoding="utf-8") as fh:
                captured["payload"] = json.load(fh)
            return _Completed(0, "done", "")

        def _fake_which(name):
            return f"/usr/bin/{name}"

        with mock.patch.object(tr.subprocess, "run", side_effect=_capture_run), \
                mock.patch.object(tr.shutil, "which", side_effect=_fake_which):
            ToolRegistry._dispatch_via_pkexec(
                _call(), "manage_packages", dict(ARGS), TOKEN,
            )

        self.assertEqual(captured["payload"]["token"], TOKEN)
        self.assertEqual(captured["payload"]["arguments"], ARGS)
        self.assertEqual(captured["payload"]["tool"], "manage_packages")


class RequestLifecycleTests(_DispatchTestCase):

    def test_no_request_is_left_behind_on_success(self):
        self._dispatch(_Completed(0, "installed", ""))
        self.assertEqual(
            self._requests_left_behind(), [],
            "a successful dispatch left an approval token on disk",
        )

    def test_no_request_is_left_behind_on_failure(self):
        self._dispatch(_Completed(1, "refused", "some stderr"))
        self.assertEqual(
            self._requests_left_behind(), [],
            "a failed dispatch left an approval token on disk",
        )

    def test_no_request_is_left_behind_on_an_authentication_refusal(self):
        self._dispatch(_Completed(126, "", "Request dismissed"))
        self.assertEqual(self._requests_left_behind(), [])

    def test_no_request_is_left_behind_when_the_call_raises(self):
        self._dispatch(raises=OSError("the call itself blew up"))
        self.assertEqual(
            self._requests_left_behind(), [],
            "an exception out of the dispatch left an approval token on disk",
        )

    def test_no_request_is_left_behind_when_the_tool_is_not_found(self):
        self._dispatch(raises=FileNotFoundError("systemd-run"))
        self.assertEqual(self._requests_left_behind(), [])

    def test_a_write_failure_fails_the_dispatch_closed(self):
        """If the request cannot be written, nothing is dispatched at all —
        the values would otherwise have to travel some other way, and the only
        other way is the command line."""
        with mock.patch.object(
            tr.privileged_request, "write_request",
            side_effect=tr.privileged_request.RequestError("no runtime dir"),
        ), mock.patch.object(tr.subprocess, "run") as run:
            result = ToolRegistry._dispatch_via_pkexec(
                _call(), "manage_packages", dict(ARGS), TOKEN,
            )
        run.assert_not_called()
        self.assertFalse(result.success)


class DiagnosticMeasuresTheNewDependencyTests(_DispatchTestCase):
    """Starting through the user manager adds a dependency. A new failure mode
    that is reported as an old one is the same false-diagnostic class that sent
    a person in the field to reinstall a package that was never broken."""

    def test_an_unreachable_user_manager_is_reported_as_such(self):
        result = self._dispatch(
            _Completed(1, "", "Failed to connect to user scope bus"),
            manager_present=False,
        )
        lowered = result.content.lower()
        self.assertTrue(
            "manager" in lowered or "systemd-run" in lowered,
            f"an unreachable user manager was not named: {result.content}",
        )

    def test_an_unreachable_manager_does_not_blame_the_runner(self):
        result = self._dispatch(
            _Completed(1, "", "Failed to connect to user scope bus"),
            manager_present=False, runner_present=True,
        )
        lowered = result.content.lower()
        self.assertNotIn("misinstalled", lowered, result.content)
        self.assertNotIn("not found", lowered, result.content)

    def test_a_missing_systemd_run_is_reported_as_such(self):
        result = self._dispatch(
            _Completed(127, "", ""), systemd_run_present=False,
        )
        self.assertIn("systemd-run", result.content)

    def test_manager_presence_is_measured_not_assumed(self):
        """Same canned result, different measured environment, different
        message. A dispatcher that never looks cannot pass this."""
        present = self._dispatch(
            _Completed(1, "", "something went wrong"), manager_present=True)
        self.recorded_argv.clear()
        absent = self._dispatch(
            _Completed(1, "", "something went wrong"), manager_present=False)
        self.assertNotEqual(
            present.content, absent.content,
            "the diagnostic reads the same whether or not a user manager "
            "exists, so its reachability is asserted rather than measured",
        )

    def test_every_measured_fact_reaches_the_user(self):
        result = self._dispatch(
            _Completed(1, "", "some stderr"), manager_present=False,
            runner_present=True,
        )
        self.assertIn("some stderr", result.content)
        self.assertIn("1", result.content)

    def test_the_runner_still_leads_when_it_spoke(self):
        runner_said = ("privileged_dispatch: dispatch token verification failed "
                       "(BadSignature): refusing dispatch.")
        result = self._dispatch(_Completed(1, runner_said, ""))
        self.assertIn(runner_said, result.content)

    def test_success_content_is_the_runner_stdout(self):
        result = self._dispatch(_Completed(0, "installed 1 package", ""))
        self.assertTrue(result.success)
        self.assertEqual(result.content, "installed 1 package")


if __name__ == "__main__":
    unittest.main()

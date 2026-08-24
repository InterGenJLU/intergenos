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
import subprocess
import tempfile
from pathlib import Path
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
        self.recorded_kwargs: list[dict] = []
        self.recorded_manager_probes: list[list[str]] = []
        self.runner_path = tr._PKEXEC_RUNNER_PATH
        self.systemd_run_path = tr._SYSTEMD_RUN

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
            # THE MANAGER PROBE IS NOT A DISPATCH (2026-08-24). The diagnostic
            # now asks the user manager about itself, through systemctl, on the
            # failure path. That is a second subprocess call, and recording it
            # beside the dispatch would make "attempted exactly once" count two
            # — turning a real invariant into a test that fails for a reason
            # that has nothing to do with retries. Route on the argv and keep
            # the probe out of the dispatch record.
            if len(argv) >= 3 and tuple(argv[1:3]) == ("--user",
                                                       "is-system-running"):
                self.recorded_manager_probes.append(list(argv))
                return _Completed(
                    0 if manager_present else 1,
                    "running\n" if manager_present else "offline\n", "",
                )
            self.recorded_argv.append(list(argv))
            self.recorded_kwargs.append(dict(kwargs))
            if raises is not None:
                raise raises
            return completed

        # REAL files, not a patched os.path.exists (changed 2026-08-24).
        # The diagnostic now distinguishes "absent" from "could not be
        # determined" and reports whether the runner is a regular file that
        # this account can execute — facts a single patched boolean cannot
        # express. A harness that cannot represent the states under test is a
        # harness that can pass a wrong answer, so the states are built on disk.
        runtime = Path(self._tmp.name)
        runner = runtime / "runner-present"
        if runner_present:
            runner.write_text("#!/bin/sh\nexit 0\n")
            runner.chmod(0o755)
        else:
            runner = runtime / "runner-absent"

        # systemd-run is a REAL path here for the same reason the runner is:
        # the diagnostic probes it with stat(), not with a PATH lookup, since
        # the constant is now absolute.
        systemd_run = runtime / "systemd-run-present"
        if systemd_run_present:
            systemd_run.write_text("#!/bin/sh\nexit 0\n")
            systemd_run.chmod(0o755)
        else:
            systemd_run = runtime / "systemd-run-absent"
        self.systemd_run_path = str(systemd_run)

        # Remembered because the patch is undone by the time a test asserts:
        # argv holds the path used DURING the dispatch, so that is what the
        # assertions must compare against, not the module constant restored
        # afterwards.
        self.runner_path = str(runner)

        with mock.patch.object(tr, "_PKEXEC_RUNNER_PATH", str(runner)), \
                mock.patch.object(tr, "_SYSTEMD_RUN", str(systemd_run)), \
                mock.patch.object(tr.subprocess, "run", side_effect=_fake_run):
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
        """The first word is the absolute systemd-run path the module names,
        not a program name for PATH to resolve on the privileged entry path."""
        self._dispatch()
        self.assertEqual(
            self.argv[:2], [self.systemd_run_path, "--user"],
            f"the runner is not being started through the user manager: "
            f"{self.argv}",
        )
        self.assertTrue(
            os.path.isabs(self.argv[0]),
            f"argv[0] is resolved through PATH: {self.argv[0]!r}",
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
        self.assertIn(self.runner_path, self.argv)
        pkexec_at = self.argv.index("pkexec")
        self.assertEqual(
            self.argv[pkexec_at + 1], self.runner_path,
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

    #: The complete set of unit properties this dispatch is allowed to set.
    #: An allowlist rather than a ban (narrowed 2026-08-24): a transient unit
    #: has no unit file, so the one legitimate bound on it — a ceiling on how
    #: long it may run — can only be expressed on the command that creates it.
    #: The ban this replaces would have been satisfied by removing that bound,
    #: which is not an improvement. Everything the ban existed to stop is still
    #: stopped, and more sharply: any property not named here fails, and the
    #: value of the one that is named must not vary with the request.
    _ALLOWED_PROPERTIES = {"RuntimeMaxSec"}

    def test_only_the_allowed_unit_properties_are_set(self):
        self._dispatch()
        for arg in self.argv:
            if arg == "-p" or arg.startswith("--property"):
                self.assertTrue(
                    arg.startswith("--property="),
                    f"a unit property is passed in a form this test cannot "
                    f"read, so it cannot be checked: {arg}",
                )
                key = arg.split("=", 1)[1].split("=", 1)[0]
                self.assertIn(
                    key, self._ALLOWED_PROPERTIES,
                    f"unexpected unit property on the dispatch: {arg}",
                )

    def test_no_hardening_property_is_imposed_on_the_unit(self):
        """No property may weaken or re-impose process hardening.

        Named explicitly rather than left to the allowlist, because this is the
        class that matters: the correction depends on the child NOT inheriting
        the daemon's restrictions, and a hardening directive smuggled onto the
        unit would undo it silently.
        """
        self._dispatch()
        joined = " ".join(self.argv)
        for directive in (
            "NoNewPrivileges", "ProtectSystem", "PrivateDevices",
            "RestrictSUIDSGID", "CapabilityBoundingSet", "SystemCallFilter",
            "ProtectKernelModules", "RestrictNamespaces",
        ):
            self.assertNotIn(
                directive, joined,
                f"the transient unit names {directive}, which is a hardening "
                f"decision this dispatch must not be making: {self.argv}",
            )

    def test_the_unit_properties_do_not_vary_with_the_request(self):
        """The user manager is a launch mechanism, not an authenticity
        boundary, so nothing the caller supplies may shape the unit."""
        def _properties():
            self.recorded_argv.clear()
            self._dispatch()
            return [a for a in self.argv if a.startswith("--property=")]

        first = _properties()
        second = _properties()
        self.assertEqual(first, second)
        self.assertTrue(first, "no properties were captured; the test is blind")


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

    def test_a_request_identifier_is_what_is_passed_instead(self):
        """Changed 2026-08-24: the argument is an IDENTIFIER, not a path.

        The runner used to receive the request file's full path. It now
        receives thirty-two hex characters and the privileged side derives the
        path itself, so a traversal or an absolute path is not something to
        reject — it is something that cannot be written down.
        """
        self._dispatch()
        runner_at = self.argv.index(self.runner_path)
        passed = self.argv[runner_at + 1:]
        self.assertEqual(
            len(passed), 1,
            f"the runner should receive exactly one argument, the request "
            f"identifier; got {passed}",
        )
        self.assertRegex(
            passed[0], r"\A[0-9a-f]{32}\Z",
            f"the runner's single argument is not a bare request identifier: "
            f"{passed[0]!r}",
        )
        self.assertNotIn(
            "/", passed[0],
            "a path separator reached the runner's command line",
        )

    def test_the_request_file_holds_what_left_the_command_line(self):
        """Proves the values were MOVED, not dropped: the token and arguments
        are in the file, which is why they are not on argv."""
        captured = {}

        def _capture_run(argv, **kwargs):
            self.recorded_argv.append(list(argv))
            # argv carries the IDENTIFIER now, so the file is reached the way
            # the privileged side reaches it: by joining the identifier to the
            # request directory. Opening argv[-1] directly would have stopped
            # working the moment a path stopped being passed, which is exactly
            # the change under test.
            request_id = argv[-1]
            path = os.path.join(
                pr.request_dir(), f"{pr.REQUEST_PREFIX}{request_id}")
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
        message. A dispatcher that never looks cannot pass this.

        Amended 2026-08-24: what is varied is the MANAGER'S ANSWER, because
        that is what the code now reads. The earlier form varied a socket path,
        and an independent review measured that path disagreeing with the
        mechanism it was standing in for.
        """
        present = self._dispatch(
            _Completed(1, "", "something went wrong"), manager_present=True)
        self.recorded_argv.clear()
        absent = self._dispatch(
            _Completed(1, "", "something went wrong"), manager_present=False)
        self.assertNotEqual(
            present.content, absent.content,
            "the diagnostic reads the same whichever way the manager answered, "
            "so its state is asserted rather than measured",
        )
        self.assertTrue(
            self.recorded_manager_probes,
            "the diagnostic never asked the manager anything",
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


class NoAutomaticRetryTests(_DispatchTestCase):
    """A dispatch whose outcome is unknown must not be attempted again.

    The approval token is consumed by the privileged side BEFORE the tool runs,
    which is right for replay protection and has a consequence worth stating:
    once an outcome is unknown, a fresh token cannot make a retry safe. A fresh
    token proves a person approved the action; it says nothing about whether the
    previous attempt already performed it. Retrying a package transaction or a
    file write that may already have happened is a worse outcome than reporting
    honestly that nobody knows.

    No retry loop exists in the dispatch path. That is asserted here rather than
    left as a property of the current shape, because "there is no loop" is the
    kind of fact a later edit changes without anyone noticing.
    """

    def test_a_failing_dispatch_is_attempted_exactly_once(self):
        for rc in (1, 126, 127, 255):
            with self.subTest(rc=rc):
                self.recorded_argv.clear()
                self._dispatch(_Completed(rc, "", "the boundary said something"))
                self.assertEqual(
                    len(self.recorded_argv), 1,
                    f"rc={rc} produced {len(self.recorded_argv)} invocations; a "
                    f"privileged action whose outcome is unknown must not be "
                    f"retried automatically",
                )

    def test_a_dispatch_that_raises_is_attempted_exactly_once(self):
        self.recorded_argv.clear()
        self._dispatch(raises=OSError("the manager went away"))
        self.assertEqual(len(self.recorded_argv), 1)

    def test_the_counter_can_see_more_than_one_invocation(self):
        """Instrument control: the assertions above are only meaningful if the
        recorder would actually report a second attempt."""
        self.recorded_argv.clear()
        self._dispatch(_Completed(1, "", "first"))
        self._dispatch(_Completed(1, "", "second"))
        self.assertEqual(len(self.recorded_argv), 2)


if __name__ == "__main__":
    unittest.main()


class TheWaitIsBoundedAndAnUnknownOutcomeSaysSoTests(_DispatchTestCase):
    """F-02 — nothing bounded a wait on a path whose whole job is to wait on a
    person.

    `subprocess.run(..., check=False)` carried no timeout, and the argv carried
    no ceiling on the unit. pkexec's purpose here is to raise an authentication
    dialog; while that dialog stands unanswered the call blocks. The same turn
    is abandoned by the browser after thirty seconds, so the person is shown a
    dead turn while a privileged action may still be pending behind it.

    Both ends are bounded now, and the case that matters is what the dispatcher
    SAYS when the bound fires. A timed-out dispatch does not know whether the
    action ran: systemd-run was the client, the unit outlived it, and the
    approval may already have been spent inside it. Reporting that as a plain
    failure would be a claim the code cannot support, and it is the claim that
    would make an automatic retry look safe.
    """

    def test_the_unit_carries_a_wall_clock_ceiling(self):
        self._dispatch()
        ceilings = [a for a in self.argv
                    if a.startswith("--property=RuntimeMaxSec=")]
        self.assertEqual(
            len(ceilings), 1,
            f"the unit has no single wall-clock ceiling: {self.argv}",
        )

    def test_the_wait_itself_is_bounded(self):
        """The unit's ceiling does not bound the CLIENT. If systemd-run itself
        wedges — a bus that never answers — the ceiling on the unit is not a
        ceiling on this call."""
        self._dispatch()
        self.assertIn(
            "timeout", self.recorded_kwargs[0],
            "subprocess.run was called with no timeout, so the wait is "
            "unbounded whatever the unit is told",
        )
        self.assertIsNotNone(self.recorded_kwargs[0]["timeout"])

    def test_the_client_bound_is_not_shorter_than_the_unit_ceiling(self):
        """Ordering matters: the unit's own ceiling should fire first, so the
        ordinary slow case produces systemd's answer rather than the client
        giving up on a unit that was about to report."""
        self._dispatch()
        self.assertGreaterEqual(
            self.recorded_kwargs[0]["timeout"], tr._DISPATCH_UNIT_MAX_SECONDS,
            "the client abandons the wait before the unit's own ceiling, so a "
            "unit that would have reported is reported as unknown instead",
        )

    def test_a_timed_out_dispatch_is_reported_as_an_unknown_outcome(self):
        result = self._dispatch(
            raises=subprocess.TimeoutExpired(cmd="systemd-run", timeout=1))
        self.assertFalse(result.success)
        lowered = result.content.lower()
        self.assertIn("not known", lowered, result.content)
        self.assertIn("may have", lowered, result.content)

    def test_a_timed_out_dispatch_never_claims_the_action_did_not_run(self):
        """The false-comfort case. 'It failed' and 'I do not know' are
        different findings, and only one of them is true here."""
        result = self._dispatch(
            raises=subprocess.TimeoutExpired(cmd="systemd-run", timeout=1))
        lowered = result.content.lower()
        for claim in ("did not run", "was not performed", "no changes were made",
                      "nothing was changed"):
            self.assertNotIn(claim, lowered, result.content)

    def test_a_timed_out_dispatch_is_attempted_exactly_once(self):
        """An unknown outcome is the one case where a retry is least safe, so
        it must not be the case where the code retries by itself."""
        self._dispatch(
            raises=subprocess.TimeoutExpired(cmd="systemd-run", timeout=1))
        self.assertEqual(
            len(self.recorded_argv), 1,
            f"a timed-out dispatch was attempted {len(self.recorded_argv)} "
            f"times",
        )

    def test_a_timed_out_dispatch_names_the_unit_so_it_can_be_looked_up(self):
        """The person is owed a way to find out what actually happened."""
        result = self._dispatch(
            raises=subprocess.TimeoutExpired(cmd="systemd-run", timeout=1))
        unit = [a for a in self.argv if a.startswith("--unit=")][0]
        self.assertIn(unit.split("=", 1)[1], result.content)

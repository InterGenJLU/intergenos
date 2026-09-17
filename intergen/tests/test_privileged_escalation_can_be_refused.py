# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A daemon can be told to refuse administrator actions, and it says which it is.

THE MEASURED CASE, 2026-09-16, on a development machine. A test cell answered a
consent gate with "allow". The registry did what it is built to do: it routed
the approved privileged call to the escalation path, the user manager ran
pkexec, pkexec asked PolicyKit, and PolicyKit raised an interactive
authentication dialog on the desktop of the person sitting at that machine —
for something they had not done, from a test run nobody was watching.

Local discipline (deny at the callback, stub the dispatcher) is what each cell
is supposed to do, and it is not enough on its own: it has to be remembered,
once per cell, forever. This is the floor underneath it. The daemon reads ONE
thing — its own environment — and under a refusing posture it declines the
escalating class at the dispatch boundary: before a request file is written,
before a unit is asked for, before any process starts. There is no path from a
refusing daemon to a password dialog.

WHAT THIS PINS.
  * the default is unchanged — an installed daemon still escalates, because
    refusing by default would quietly take away a person's ability to change
    their own machine through the assistant;
  * a shipped unit file does not set the variable, so the default is what
    installs actually run;
  * anything other than the one permitting value refuses, so a typo cannot be
    read as permission;
  * the posture comes from the daemon's environment and from nothing a turn
    carries;
  * the trace states the posture at every privileged dispatch, on BOTH
    postures, so a daemon that refuses is never mistaken for one that enforces
    by escalating — and the record says which was running;
  * the test harness sets the refusing value for every test process, so a cell
    written later that forgets to stub the dispatcher still cannot reach a
    prompt.

NOTHING HERE INVOKES pkexec. The one cell that proves the escalating path is
still reachable by default stubs the subprocess boundary and reads the argument
vector; it never runs it. There is no environment scrub that makes a real pkexec
call unattended-safe — PolicyKit talks to the agent registered for the desktop
session, whatever the child process's environment says — so the call is not made
at all.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from intergen.interfaces.types import Provenance, ToolCall

ENV = "INTERGEN_PRIVILEGED_ESCALATION"


def _privileged_call() -> ToolCall:
    return ToolCall(name="manage_services",
                    arguments={"action": "restart", "service": "sshd"},
                    call_id="c1", source_of_request=Provenance.USER_DIRECT)


class _PostureEnv:
    """Set, clear and restore the one variable, without touching anything else."""

    def __init__(self, value: str | None) -> None:
        self._value = value
        self._saved: str | None = None
        self._had = False

    def __enter__(self):
        self._had = ENV in os.environ
        self._saved = os.environ.get(ENV)
        if self._value is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = self._value
        return self

    def __exit__(self, *exc):
        if self._had:
            os.environ[ENV] = self._saved  # type: ignore[assignment]
        else:
            os.environ.pop(ENV, None)
        return False


class ThePostureIsReadFromTheDaemonsOwnEnvironment(unittest.TestCase):
    def test_nothing_set_means_the_installed_default_escalates(self):
        from intergen.tool_registry import privileged_escalation_posture
        with _PostureEnv(None):
            posture = privileged_escalation_posture()
        self.assertTrue(posture.allows)
        self.assertEqual(posture.source, "default")
        self.assertEqual(posture.setting, "")

    def test_the_one_permitting_value_allows_and_names_itself(self):
        from intergen.tool_registry import (
            ESCALATION_ALLOWED, privileged_escalation_posture,
        )
        with _PostureEnv(ESCALATION_ALLOWED):
            posture = privileged_escalation_posture()
        self.assertTrue(posture.allows)
        self.assertEqual(posture.source, "environment")

    def test_anything_else_refuses(self):
        """A typo is not permission. Fail closed on every unrecognised value."""
        from intergen.tool_registry import privileged_escalation_posture
        for value in ("refuse", "", "off", "0", "1", "true", "yes", "allowed",
                      "Allow", " allow"):
            with self.subTest(value=value), _PostureEnv(value):
                self.assertFalse(privileged_escalation_posture().allows,
                                 f"{value!r} was read as permission")


class _NoEscalationPossible:
    """Stub EVERY seam between this process and pkexec, unconditionally.

    BELT AND BRACES, and the braces are the point. The posture under test is
    what SHOULD stop a dispatch, but a cell must never DEPEND on the behaviour
    it is testing to stay safe: run these same cells against a tree without the
    posture — a red run, a bisect, an older checkout — and the call goes
    straight through to systemd-run, pkexec and PolicyKit.

    That is not hypothetical. Authoring this file on 2026-09-16, the red run
    against the unmodified base tree did exactly that: two cells reached the
    real dispatch path, pkexec ran twice, and the person at the machine was
    shown an authentication dialog and answered it. The posture was absent at
    the base — which is the whole reason a red run exists.

    So nothing here reaches a process. subprocess.run is stubbed, and so is
    every request-file helper, so no cell can write, read or unlink a file
    either.
    """

    def __init__(self) -> None:
        self._patches = []
        self.run = None

    def __enter__(self):
        import intergen.tool_registry as reg_mod
        self._patches = [
            mock.patch.object(reg_mod.subprocess, "run"),
            mock.patch.object(reg_mod.privileged_request, "write_request",
                              return_value="/nonexistent/request"),
            mock.patch.object(reg_mod.privileged_request, "request_id_for",
                              return_value="deadbeef"),
            mock.patch.object(reg_mod.privileged_request,
                              "prune_stale_requests", return_value=[]),
            mock.patch.object(reg_mod.privileged_request, "discard_request"),
        ]
        started = [p.start() for p in self._patches]
        self.run = started[0]
        self.run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class ARefusingDaemonNeverReachesTheAuthenticationPath(unittest.TestCase):
    """The boundary cell. Nothing is staged, nothing is started."""

    # No _dispatch() helper: the call is written out inside each cell's stub
    # context. A helper would put the one dangerous line outside every `with`,
    # where the rule below could not see whether it was guarded — and a rule
    # that cannot see the call is not a rule.

    def test_the_dispatch_is_refused_before_anything_runs(self):
        import intergen.tool_registry as reg_mod

        from intergen.tool_registry import ToolRegistry
        with _PostureEnv("refuse"), _NoEscalationPossible() as stubs, \
                mock.patch.object(reg_mod.privileged_request, "write_request") \
                as write_request:
            result = ToolRegistry._dispatch_via_pkexec(
                _privileged_call(), "manage_services",
                {"action": "restart", "service": "sshd"},
                dispatch_token="a-token-that-must-never-be-used")

        stubs.run.assert_not_called()
        write_request.assert_not_called()
        self.assertFalse(result.success)
        self.assertFalse(result.executed)

    def test_the_person_is_told_plainly_what_happened(self):
        from intergen.tool_registry import ToolRegistry
        with _PostureEnv("refuse"), _NoEscalationPossible():
            result = ToolRegistry._dispatch_via_pkexec(
                _privileged_call(), "manage_services",
                {"action": "restart", "service": "sshd"},
                dispatch_token="a-token-that-must-never-be-used")
        low = result.content.lower()
        self.assertIn("manage_services", result.content)
        self.assertNotIn(ENV.lower(), low,
                         "the refusal names an environment variable at the "
                         "person instead of saying what happened")
        self.assertIn("nothing was changed", low)

    def test_the_default_posture_still_reaches_the_escalating_path(self):
        """RED FOR THIS ITEM: with nothing set, the dispatch escalates.

        The proof reads the argument vector the boundary would have run and
        stops there. It is never executed: a real pkexec call raises a dialog on
        whatever desktop is registered with PolicyKit, and no environment scrub
        prevents that.
        """
        from intergen.tool_registry import ToolRegistry
        with _PostureEnv(None), _NoEscalationPossible() as stubs:
            ToolRegistry._dispatch_via_pkexec(
                _privileged_call(), "manage_services",
                {"action": "restart", "service": "sshd"},
                dispatch_token="a-token-that-must-never-be-used")

        stubs.run.assert_called_once()
        argv = stubs.run.call_args[0][0]
        self.assertIn("pkexec", argv,
                      "the default posture no longer reaches the escalation "
                      "path; this cell is the proof that the refusing posture "
                      "is doing something, so re-point it before changing it")

    def test_no_turn_input_can_lift_the_refusal(self):
        """The posture is not negotiable by anything a request carries."""
        from intergen.tool_registry import ToolRegistry

        call = ToolCall(
            name="manage_services",
            arguments={"action": "restart", "service": "sshd",
                       ENV: "allow", "privileged_escalation": "allow",
                       "allow_escalation": True},
            call_id="c1", source_of_request=Provenance.USER_DIRECT)
        with _PostureEnv("refuse"), _NoEscalationPossible():
            result = ToolRegistry._dispatch_via_pkexec(
                call, "manage_services", dict(call.arguments),
                dispatch_token="a-token-that-must-never-be-used")
        self.assertFalse(result.success)
        self.assertFalse(result.executed)


class TheTraceStatesThePostureOnBothSides(unittest.TestCase):
    """A refusing daemon must never be mistakable for an escalating one."""

    def _rows(self, posture_value):
        import intergen.tool_registry as reg_mod

        rows = []
        with _PostureEnv(posture_value), _NoEscalationPossible(), \
                mock.patch.object(reg_mod.glass, "emit",
                                  side_effect=lambda *a, **k: rows.append((a, k))):
            reg_mod.ToolRegistry._dispatch_via_pkexec(
                _privileged_call(), "manage_services",
                {"action": "restart", "service": "sshd"},
                dispatch_token="a-token-that-must-never-be-used")
        return [k["detail"] for a, k in rows
                if a[:2] == ("dispatch", "privileged_escalation")]

    def test_a_refusing_dispatch_says_so(self):
        details = self._rows("refuse")
        self.assertEqual(len(details), 1)
        self.assertFalse(details[0]["escalates"])
        self.assertEqual(details[0]["setting"], "refuse")
        self.assertEqual(details[0]["source"], "environment")
        self.assertEqual(details[0]["tool"], "manage_services")

    def test_an_escalating_dispatch_says_so_too(self):
        """The row is emitted on BOTH postures.

        If only the refusal were recorded, a daemon that escalates would be
        recognisable only by the ABSENCE of a line — and an absent line is also
        what an older build, a crash before the boundary, or a dropped trace
        looks like.
        """
        details = self._rows(None)
        self.assertEqual(len(details), 1)
        self.assertTrue(details[0]["escalates"])
        self.assertEqual(details[0]["source"], "default")


class NoCellInThisFileMayReachTheEscalationPath(unittest.TestCase):
    """The rule that keeps the correction from being edited away.

    Every cell that calls the dispatch must hold the stub context while it does
    it. Read from this file's own syntax tree, because the failure it guards
    against is a NEW cell written later by someone who did not read the comment
    above the stub.
    """

    def test_every_dispatch_call_is_inside_the_stub_context(self):
        import ast
        from pathlib import Path

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

        def guarded(node) -> bool:
            """True when this statement sits inside a _NoEscalationPossible with."""
            for parent in ast.walk(tree):
                if not isinstance(parent, ast.With):
                    continue
                names = [ast.dump(i.context_expr) for i in parent.items]
                if not any("_NoEscalationPossible" in n for n in names):
                    continue
                for inner in ast.walk(parent):
                    if inner is node:
                        return True
            return False

        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and (getattr(n.func, "attr", "") == "_dispatch_via_pkexec"
                      or getattr(n.func, "attr", "") == "_dispatch")]
        self.assertTrue(calls, "no dispatch call found; re-point this cell")
        unguarded = [n.lineno for n in calls if not guarded(n)]
        self.assertEqual(
            unguarded, [],
            f"lines {unguarded} call the privileged dispatch without the stub "
            f"context. On a tree where the refusing posture does not exist "
            f"(a red run, a bisect, an older checkout) that call reaches "
            f"pkexec and PolicyKit raises a password dialog on whatever "
            f"desktop is registered — measured, on this file, 2026-09-16.")


class TheShippedInstallKeepsTheDefault(unittest.TestCase):
    """What users run must not carry the test harness's posture."""

    @staticmethod
    def _tree_file(*parts):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        return root.joinpath(*parts)

    def test_no_shipped_unit_or_policy_file_sets_the_variable(self):
        shipped = [
            self._tree_file("intergen", "data", "intergen.service"),
            self._tree_file("intergen", "data",
                            "com.intergenos.InterGen.service"),
            self._tree_file("intergen", "data",
                            "org.intergenos.intergen.policy"),
            self._tree_file("intergen", "data", "intergen-privileged-runner"),
        ]
        for path in shipped:
            with self.subTest(path=path.name):
                self.assertTrue(path.exists(), f"{path} is missing from the tree")
                self.assertNotIn(ENV, path.read_text(encoding="utf-8"),
                                 f"{path.name} sets the escalation posture; a "
                                 f"shipped install must run the default")

    def test_the_default_is_what_an_unset_environment_gives(self):
        from intergen.tool_registry import privileged_escalation_posture
        with _PostureEnv(None):
            self.assertTrue(privileged_escalation_posture().allows)


class TheHarnessSetsItForEveryTestProcess(unittest.TestCase):
    def test_this_very_process_cannot_escalate(self):
        """Read from the live environment of the running test process.

        Not a re-read of conftest's source: what matters is that the process
        executing this cell — and therefore every other cell — holds a refusing
        posture right now.
        """
        from intergen.tool_registry import privileged_escalation_posture
        self.assertEqual(os.environ.get(ENV), "refuse")
        self.assertFalse(privileged_escalation_posture().allows)


if __name__ == "__main__":
    unittest.main()

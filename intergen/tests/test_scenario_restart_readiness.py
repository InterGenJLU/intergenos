# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Restart-before hardening — serving readiness, settle, and the reset ordering.

Measured facts from a live persistence run drive these fixtures:

* the unit is ``Type=dbus``, so ``systemctl restart`` returns when the bus NAME
  is acquired; the dbus readiness gate then accepted a router-only daemon, and a
  deterministic probe answer kept it from noticing the model was down — the gate
  returned ~48 ms after the unit reported Started, while the daemon was still in
  its model port ladder;
* a restart that loses that port race (a prior socket has not released) comes up
  bus-present but model-degraded and bounces AGAIN underneath the caller, so a
  later bus call — the next scenario's ResetConversation — lands in a teardown
  window and aborts the run;
* the reset ordering is the adjacent instance of the same class: a scenario whose
  FIRST turn declares restart-before used to reset into the very daemon that
  boundary was about to bounce. (The shipped persistence corpus carries its
  boundary on turn 2, so this ordering was not the abort measured on that run —
  it is the same hazard on a different turn index, closed here.)

The shapes below are the daemon's own Status payload fields, not invented ones.
"""

from __future__ import annotations

import unittest

from intergen.tests.client import serving_readiness
from intergen.tests.scenario.grader import grade_turn
from intergen.tests.scenario.runner import run_scenario
from intergen.tests.scenario.schema import Scenario, Turn
from intergen.tests.scenario.transport import MockTransport, TurnResult


def _status(router=True, llama=True, integrity=None) -> dict:
    """A Status payload in the daemon's own shape."""
    return {
        "running": True,
        "model_server_integrity_failure": integrity,
        "components": {
            "hardware_detector": True,
            "model_manager": True,
            "llama_server": llama,
            "router": router,
            "semantic_matcher": True,
            "tools": True,
            "memory": True,
            "watchdog": True,
        },
    }


def _scn(sid: str, turns: list[Turn]) -> Scenario:
    return Scenario(id=sid, name=sid, axis=["memory_persistence"],
                    category="memory_personal", turns=turns)


class ServingReadinessTests(unittest.TestCase):
    def test_healthy_daemon_is_ready(self):
        ready, reason = serving_readiness(_status(), endpoint_healthy=True)
        self.assertTrue(ready)
        self.assertEqual(reason, "")

    def test_degraded_up_daemon_fails_the_gate_loudly(self):
        # THE measured shape: the restart lost the port race, so the daemon's own
        # model server is not running while the router is built and the bus name
        # is claimed. The old gate returned ready here.
        ready, reason = serving_readiness(_status(llama=False),
                                          endpoint_healthy=True)
        self.assertFalse(ready)
        self.assertIn("model server is not running", reason)

    def test_router_not_built_is_named(self):
        ready, reason = serving_readiness(_status(router=False),
                                          endpoint_healthy=True)
        self.assertFalse(ready)
        self.assertIn("router", reason)

    def test_endpoint_down_fails_even_with_a_running_handle(self):
        ready, reason = serving_readiness(_status(), endpoint_healthy=False)
        self.assertFalse(ready)
        self.assertIn("/health", reason)

    def test_integrity_failure_is_never_graded_against(self):
        ready, reason = serving_readiness(
            _status(integrity="cpu baseline refused"), endpoint_healthy=True)
        self.assertFalse(ready)
        self.assertIn("integrity", reason)

    def test_unreadable_status_is_not_a_verdict(self):
        ready, reason = serving_readiness(
            {"error": "recipient disconnected"}, endpoint_healthy=True)
        self.assertFalse(ready)
        self.assertIn("unreadable", reason)

    def test_both_handle_and_endpoint_are_required(self):
        # Neither signal alone is sufficient: a departing instance's server can
        # still answer /health, and a running handle does not prove the port
        # answers. Every single-signal combination must fail.
        for llama, endpoint in ((True, False), (False, True), (False, False)):
            with self.subTest(llama=llama, endpoint=endpoint):
                ready, _ = serving_readiness(_status(llama=llama),
                                             endpoint_healthy=endpoint)
                self.assertFalse(ready)


class UserSuppliedDevicePathTests(unittest.TestCase):
    """The store turn of a persistence scenario names a device on purpose."""

    def test_echoing_a_path_the_user_gave_is_not_hallucination(self):
        turn = Turn(user="remember that my backup drive is /dev/sdb1")
        grade = grade_turn(turn, TurnResult(text="Got it — /dev/sdb1 noted."),
                           category="memory")
        r = [x for x in grade.results if x.type == "no_hallucinated_device_path"]
        self.assertTrue(r[0].passed, r[0].actual)

    def test_a_path_the_question_never_named_still_hard_fails(self):
        turn = Turn(user="what's my backup drive?")
        grade = grade_turn(turn, TurnResult(text="It's /dev/sdc9."),
                           category="memory")
        r = [x for x in grade.results if x.type == "no_hallucinated_device_path"]
        self.assertFalse(r[0].passed)
        self.assertEqual(r[0].gate, "A")
        self.assertIn("/dev/sdc9", r[0].actual)


class _DisconnectingResetTransport(MockTransport):
    """A daemon that is being bounced: reset() into it fails the way the live one
    did, until the restart boundary has actually been taken.

    This is the abort the run hit, reproduced with no daemon: the scenario-start
    reset was issued while the process was going away, and the bus call came back
    "recipient disconnected".
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.bouncing = True

    def reset(self) -> None:
        if self.bouncing:
            raise RuntimeError(
                "InterGen ResetConversation D-Bus call failed: Message recipient "
                "disconnected from message bus without replying — cannot reset "
                "per-conversation state; aborting rather than grading a "
                "contaminated run.")
        super().reset()

    def restart_daemon(self) -> None:
        super().restart_daemon()
        self.bouncing = False   # the bounce is taken; the daemon is fresh


class ResetOrderingTests(unittest.TestCase):
    def test_first_turn_boundary_is_applied_before_the_scenario_reset(self):
        s = _scn("R1", [Turn(user="recall my timezone",
                             session_marker="restart-before")])
        t = MockTransport()
        run_scenario(s, t)
        self.assertLess(t.calls.index("restart_daemon"), t.calls.index("reset"),
                        f"boundary must precede the scenario reset: {t.calls}")
        self.assertEqual(t.calls[-1], "ask:recall my timezone")
        self.assertEqual(run_scenario(s, MockTransport()).boundaries,
                         ["restart-before"])

    def test_the_recorded_abort_no_longer_happens(self):
        # RED before the ordering fix (reset ran first, into the bouncing
        # daemon); GREEN after it.
        s = _scn("R2", [Turn(user="what did I tell you",
                             session_marker="restart-before")])
        t = _DisconnectingResetTransport()
        run = run_scenario(s, t)                      # must not raise
        self.assertEqual(run.boundaries, ["restart-before"])
        self.assertEqual(t.reset_count, 1)

    def test_a_reset_into_a_bouncing_daemon_still_fails_loud(self):
        # The ordering fix must not have softened the reset contract: a scenario
        # with no boundary on its first turn still resets first, and a daemon
        # that is going away still aborts the run rather than grade it.
        s = _scn("R3", [Turn(user="hello")])
        with self.assertRaises(RuntimeError) as ctx:
            run_scenario(s, _DisconnectingResetTransport())
        self.assertIn("aborting rather than grading", str(ctx.exception))

    def test_boundary_on_a_later_turn_is_unchanged(self):
        s = _scn("R4", [Turn(user="remember: my timezone is CDT"),
                        Turn(user="what is my timezone",
                             session_marker="restart-before")])
        t = MockTransport()
        run = run_scenario(s, t)
        self.assertEqual(run.boundaries, ["restart-before"])
        # reset first (no first-turn boundary), then turn 1, then the boundary.
        self.assertEqual(
            t.calls,
            ["reset", "ask:remember: my timezone is CDT", "restart_daemon",
             "await_ready", "ask:what is my timezone"])

    def test_new_session_boundary_gets_the_same_ordering(self):
        s = _scn("R5", [Turn(user="recall", session_marker="new-session-before")])
        t = MockTransport()
        run_scenario(s, t)
        self.assertLess(t.calls.index("new_session"), t.calls.index("reset"),
                        f"boundary must precede the scenario reset: {t.calls}")


if __name__ == "__main__":
    unittest.main()

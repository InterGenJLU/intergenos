# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-4.1 — the live cross-posture run driver, exercised with no daemon/bus/model.

The driver is the one harness module that talks to a live daemon; its structure
(per-declared-posture subset selection, per-posture artifacts, the shared-scope
cross-posture diff, PASS-rate over --repeat, and the transport lifecycle) is
pinned here against the mock transport, so the whole pipeline is deterministic
with no model. The real ClientTransport(mode="dbus") is exercised against the
live 9B daemon by the scheduled tier run; here the CONTRACT is the test.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.scenario.live_run import (
    consent_observations_from_glass,
    build_trace_lookup,
    cross_posture_diff,
    run_live,
    scenarios_for_posture,
)
from intergen.tests.scenario.schema import Assertion, Scenario, Turn
from intergen.tests.scenario.transport import MockTransport, TurnResult


def _scn(sid: str, postures: list[str], *, route: str = "keyword") -> Scenario:
    """A one-turn scenario asserting a route source — enough to grade PASS/FAIL
    off the mock reply's source, per posture."""
    return Scenario(
        id=sid, name=sid, axis=["routing"], postures=list(postures),
        capabilities=[f"cap:{sid}"],
        turns=[Turn(user=f"msg-{sid}",
                    assertions=[Assertion("routes_via", route)])],
    )


def _factory(source: str = "keyword"):
    """A transport factory returning a fresh MockTransport whose every reply
    carries ``source`` (so routes_via(source) passes) and a trace_id."""
    def make() -> MockTransport:
        return MockTransport(default=TurnResult(text="ok", source=source,
                                                trace_id="t-1"))
    return make


class SubsetSelectionTests(unittest.TestCase):
    def test_scenarios_for_posture_declared_only(self):
        scns = [_scn("A", ["2B-locked"]),
                _scn("B", ["2B-locked", "9B-native"]),
                _scn("C", ["9B-native"])]
        self.assertEqual([s.id for s in scenarios_for_posture(scns, "2B-locked")],
                         ["A", "B"])
        self.assertEqual([s.id for s in scenarios_for_posture(scns, "9B-native")],
                         ["B", "C"])


class RunLiveArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="live-run-test-")
        self.scns = [_scn("A", ["2B-locked"]),
                     _scn("B", ["2B-locked", "9B-native"]),
                     _scn("C", ["9B-native"])]

    def test_per_posture_artifacts_and_subset(self):
        m = run_live(self.scns, _factory("keyword"),
                     ["2B-locked", "9B-native"], self.tmp, "run1")
        base = Path(self.tmp) / "run1"
        # Each posture writes results.json + summary.txt over its declared subset.
        for posture, expect in (("2B-locked", ["A", "B"]), ("9B-native", ["B", "C"])):
            res = json.loads((base / posture / "results.json").read_text())
            self.assertEqual([s["id"] for s in res["scenarios"]], expect)
            self.assertTrue((base / posture / "summary.txt").exists())
            self.assertEqual(m["postures"][posture]["scenarios_run"], expect)
        self.assertTrue((base / "manifest.json").exists())

    def test_cross_posture_diff_scoped_to_shared(self):
        m = run_live(self.scns, _factory("keyword"),
                     ["2B-locked", "9B-native"], self.tmp, "run2")
        diff = json.loads((Path(self.tmp) / "run2" / "cross-posture-diff.json").read_text())
        # Only B declares both postures -> the head-to-head is B alone; A/C are
        # posture-exclusive, reported as scope, never as dropped/regression.
        self.assertEqual(diff["counts"]["old_scenarios"], 1)
        self.assertEqual(diff["counts"]["new_scenarios"], 1)
        self.assertEqual(diff["dropped_scenarios"], [])
        self.assertEqual(diff["posture_exclusive"]["baseline_only"], ["A"])
        self.assertEqual(diff["posture_exclusive"]["candidate_only"], ["C"])
        self.assertFalse(diff["regression"])
        self.assertEqual(m["cross_posture_diff"]["regression"], False)

    def test_repeat_records_pass_rate_and_rep_dirs(self):
        m = run_live(self.scns, _factory("keyword"),
                     ["2B-locked"], self.tmp, "run3", repeat=3, compare=False)
        pr = m["postures"]["2B-locked"]["pass_rate"]
        self.assertEqual(pr["A"], {"pass": 3, "of": 3, "grades": ["PASS"] * 3})
        for rep in (1, 2, 3):
            self.assertTrue(
                (Path(self.tmp) / "run3" / "2B-locked" / f"rep-{rep:02d}" /
                 "results.json").exists())

    def test_reuse_one_transport_by_default(self):
        made: list[MockTransport] = []
        base = _factory("keyword")

        def counting():
            t = base()
            made.append(t)
            return t

        run_live(self.scns, counting, ["2B-locked", "9B-native"],
                 self.tmp, "run4")
        # Default: one transport reused across both postures (the same-live-daemon
        # leg), closed once at the end.
        self.assertEqual(len(made), 1)
        self.assertTrue(made[0].closed)

    def test_reconnect_between_postures_builds_fresh(self):
        made: list[MockTransport] = []
        base = _factory("keyword")

        def counting():
            t = base()
            made.append(t)
            return t

        run_live(self.scns, counting, ["2B-locked", "9B-native"],
                 self.tmp, "run5", reconnect_between_postures=True)
        # One transport per posture (restart-the-box-into-the-tier leg), each closed.
        self.assertEqual(len(made), 2)
        self.assertTrue(all(t.closed for t in made))

    def test_await_ready_called_before_running(self):
        made: list[MockTransport] = []
        base = _factory("keyword")

        def counting():
            t = base()
            made.append(t)
            return t

        run_live(self.scns, counting, ["2B-locked"], self.tmp, "run6", compare=False)
        # The fail-closed readiness gate must be hit before any turn is asked.
        self.assertGreaterEqual(made[0].ready_calls, 1)
        self.assertTrue(made[0].asked)


class CrossPostureDiffRegressionTests(unittest.TestCase):
    def test_grade_drop_on_shared_scenario_is_regression(self):
        # Same shared scenario B: PASS under 2B (route matches) -> FAIL under 9B
        # (route differs) is a real cross-posture regression on the shared cell.
        scns = [_scn("B", ["2B-locked", "9B-native"], route="keyword")]
        tmp = tempfile.mkdtemp(prefix="live-run-reg-")

        def factory_2b():
            return MockTransport(default=TurnResult(text="ok", source="keyword",
                                                    trace_id="t"))
        # A single transport whose source flips would need per-posture control;
        # drive the two legs through cross_posture_diff directly instead.
        from intergen.tests.scenario import report
        from intergen.tests.scenario.runner import run_scenarios
        good = run_scenarios(scns, factory_2b(), posture="2B-locked")
        res_2b = report.build_results(good, scns, "b-2b")
        bad = MockTransport(default=TurnResult(text="ok", source="llm_freeform",
                                               trace_id="t"))
        res_9b = report.build_results(
            run_scenarios(scns, bad, posture="9B-native"), scns, "b-9b")
        diff = cross_posture_diff(res_2b, res_9b)
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["grade_regressions"][0]["id"], "B")


class TraceLookupTests(unittest.TestCase):
    def test_reply_only_lookup_carries_route_and_tools(self):
        lk = build_trace_lookup()
        tr = TurnResult(text="hi", source="llm_tools", trace_id="t-9",
                        tool_calls=[{"name": "web_search",
                                     "arguments": {"query": "x"}}])
        view = lk(tr)
        self.assertEqual(view.route_source, "llm_tools")
        self.assertEqual(view.tools_called, ["web_search"])
        # No decisions capture -> outcomes unresolved -> grounding fails closed.
        self.assertFalse(view.outcomes_resolved)

    def test_decisions_capture_resolves_outcomes(self):
        lk = build_trace_lookup(
            decisions_rows=[{"trace_id": "t-9",
                             "attributes": {"dispatch_any_failed": True}}])
        tr = TurnResult(text="hi", source="llm_tools", trace_id="t-9",
                        tool_calls=[{"name": "run_command", "arguments": {}}])
        view = lk(tr)
        self.assertTrue(view.outcomes_resolved)
        self.assertTrue(view.any_dispatch_not_ok())

    def test_glass_supplies_sub_queries(self):
        lk = build_trace_lookup(
            glass_rows=[{"turn_id": "t-9", "phase": "decision",
                         "event": "decompose",
                         "detail": {"sub_queries": ["a", "b"]}}])
        tr = TurnResult(text="hi", source="decomposed", trace_id="t-9")
        self.assertEqual(lk(tr).sub_queries, ["a", "b"])


class AwaitReadyRoutingTests(unittest.TestCase):
    """ClientTransport.await_ready must route to the MODE-appropriate readiness
    gate. Regression pin for the first-live-dbus-run bug: selecting the gate by a
    single name always resolved the direct gate, which in dbus mode dereferenced
    a None in-process daemon and failed closed only after the full timeout."""

    class _StubClient:
        def __init__(self):
            self.called: list = []

        def _await_ready(self, timeout_s=None):
            self.called.append(("direct", timeout_s))

        def _await_ready_dbus(self, timeout_s=None):
            self.called.append(("dbus", timeout_s))

    def _transport(self, mode: str):
        from intergen.tests.scenario.transport import ClientTransport
        t = ClientTransport.__new__(ClientTransport)   # bypass real client construction
        t._mode = mode
        t._client = self._StubClient()
        return t

    def test_dbus_mode_uses_the_dbus_gate(self):
        t = self._transport("dbus")
        t.await_ready(5)
        self.assertEqual(t._client.called, [("dbus", 5)])

    def test_direct_mode_uses_the_direct_gate(self):
        t = self._transport("direct")
        t.await_ready(5)
        self.assertEqual(t._client.called, [("direct", 5)])


if __name__ == "__main__":
    unittest.main()


class EvalConsentGateTests(unittest.TestCase):
    """--require-eval-consent is a fail-closed precondition, not advice.

    An unattended baseline exists to run without a human at the keyboard. If the
    daemon under test has not got the deny-and-record responder armed, the run
    will stall on a consent modal — so the driver refuses to grade it rather than
    discovering the problem hours later as a hung leg.
    """

    class _StatusTransport(MockTransport):
        """MockTransport whose status() carries a chosen eval_consent block."""

        def __init__(self, eval_consent_block, **kw):
            super().__init__(**kw)
            self._eval_consent_block = eval_consent_block

        def status(self):
            st = super().status()
            if self._eval_consent_block is not None:
                st["eval_consent"] = self._eval_consent_block
            return st

    def _factory_with(self, block):
        def make():
            return self._StatusTransport(
                block, default=TurnResult(text="ok", source="keyword",
                                          trace_id="t-1"))
        return make

    def _run(self, block):
        scns = [_scn("A", ["2B-locked"])]
        with tempfile.TemporaryDirectory() as td:
            return run_live(scns, self._factory_with(block), ["2B-locked"],
                            td, "r1", trace_lookup=build_trace_lookup(),
                            compare=False, require_eval_consent=True)

    def test_armed_daemon_runs(self):
        manifest = self._run({"armed": True, "policy": "deny_and_record",
                              "denials": 0})
        self.assertEqual(manifest["postures"]["2B-locked"]["count"], 1)

    def test_unarmed_daemon_refuses_to_grade(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run({"armed": False, "policy": None, "denials": 0})
        self.assertIn("NOT armed", str(ctx.exception))
        self.assertIn("--eval-consent-deny", str(ctx.exception))

    def test_daemon_without_the_status_key_is_treated_as_unarmed(self):
        """An unknown posture is never assumed to be the safe one."""
        with self.assertRaises(RuntimeError):
            self._run(None)

    def test_armed_false_is_not_satisfied_by_a_truthy_block(self):
        with self.assertRaises(RuntimeError):
            self._run({"policy": "deny_and_record", "denials": 7})

    def test_no_gate_when_not_required(self):
        """Default runs are unaffected — the flag is opt-in."""
        scns = [_scn("A", ["2B-locked"])]
        with tempfile.TemporaryDirectory() as td:
            manifest = run_live(scns, self._factory_with(None), ["2B-locked"],
                                td, "r1", trace_lookup=build_trace_lookup(),
                                compare=False)
        self.assertEqual(manifest["postures"]["2B-locked"]["count"], 1)

    def test_roll_up_lands_in_the_manifest(self):
        block = {"armed": True, "policy": "deny_and_record", "denials": 3,
                 "per_gate": {"action_review": 3}}
        manifest = self._run(block)
        self.assertEqual(
            manifest["postures"]["2B-locked"]["eval_consent"]["denials"], 3)


class ConsentObservationExtractionTests(unittest.TestCase):
    """Denials are attributed back to the turn that provoked them."""

    def test_extracts_denials_with_turn_correlation(self):
        rows = [
            {"phase": "consent", "event": "eval_armed", "detail": {}},
            {"phase": "consent", "event": "eval_denied",
             "detail": {"gate": "action_review", "verdict": "deny",
                        "action": "run_command", "turn_id": "turn-7"}},
            {"phase": "route", "event": "turn_start", "detail": {}},
            {"phase": "consent", "event": "eval_denied",
             "detail": {"gate": "phone_a_friend_send", "verdict": "deny",
                        "action": "send->openai", "turn_id": "turn-9"}},
        ]
        obs = consent_observations_from_glass(rows)
        self.assertEqual([o["event"] for o in obs],
                         ["eval_armed", "eval_denied", "eval_denied"])
        denials = [o for o in obs if o["event"] == "eval_denied"]
        self.assertEqual(denials[0]["turn_id"], "turn-7")
        self.assertEqual(denials[0]["gate"], "action_review")
        self.assertEqual(denials[1]["action"], "send->openai")

    def test_ignores_unrelated_rows(self):
        self.assertEqual(consent_observations_from_glass(
            [{"phase": "route", "event": "turn_start"},
             {"phase": "consent", "event": "something_else"}]), [])

    def test_no_rows_is_empty_not_an_error(self):
        self.assertEqual(consent_observations_from_glass(None), [])

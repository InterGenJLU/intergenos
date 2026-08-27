# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A turn the harness could not drive is never graded, and a dead engine stops the run.

WHAT HAPPENED, AND WHY THIS FILE EXISTS. On 2026-08-26 between 18:19 and 18:22 the 2B
laptop's graphics engine hung and took llama-server down with it (i915 hang ->
SIGABRT). Every model call after that got CONNECTION REFUSED — no HTTP response at all.
The scenario harness did not notice. It AWARDED FOUR PASSES with no model behind them
(WRT-05 in 0.4 s, WRT-06 in 1.2 s, WRT-08 in 0.7 s, and WRT-04 straight through the hang
window) and printed "0 could not be driven".

That is the worst failure a proof harness can have. A FAIL is information. A PASS with
nothing behind it is a false statement about the product, and it is the statement most
likely to be believed and least likely to be re-checked. Two things were wrong:

  1. THE TURN'S FAILURE WAS INVISIBLE. intergen/llm.py catches the transport exception,
     writes one line to the log and returns nothing. No exception reaches the harness,
     no trace event records it, and the router's degraded fallback produces a reply that
     looks like any other. Speed was the only tell, and speed is not an assertion.
  2. NOTHING COUNTED. Even had one turn been noticed, the run would have continued
     through every remaining scenario, spending an hour measuring nothing.

WHAT IS PINNED HERE
  * A transport that cannot get a response raises TransportRefused, which is a distinct
    type — not an anonymous Exception that the run loop's blanket handler folds in with
    a scenario that genuinely errored.
  * A scenario whose turn is refused is ABANDONED, not graded. It gets no PASS and no
    FAIL, because both would be claims about the product that this run cannot support.
  * TWO CONSECUTIVE undriveable turns ABORT the run, with a named reason and a non-zero
    exit, and the abort is written into summary.txt where a person reading the artifacts
    will see it.
  * A run that recovers does not abort: the consecutive counter RESETS on a turn that
    drove. One refused turn is a blip; two in a row is a dead engine.

Everything here runs with no daemon, no bus and no model, against a stub transport that
refuses after turn k — which is the only way to test this deterministically, since the
real condition requires killing an engine mid-run.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from intergen.tests.scenario.transport import (
    MockTransport, ScenarioTransport, TransportRefused, TurnResult)


def _scenario(sid: str, turns: int = 2):
    """A minimal scenario with `turns` user turns and no assertions.

    Built through the real loader so the shape this file drives is the shape the
    harness drives, not a hand-rolled stand-in that could drift from it.
    """
    from intergen.tests.scenario.loader import parse_scenario
    return parse_scenario({
        "id": sid,
        "name": f"stub scenario {sid}",
        "axis": ["routing"],
        "tags": ["harness-selftest"],
        # One explicit assertion per turn: the loader REFUSES a vacuous turn, and
        # rightly — an always-pass turn is the same class of lie this whole file is
        # about. The assertion itself is irrelevant here; what is under test is
        # whether the turn is ever GRADED at all.
        "turns": [{"user": f"turn {i}", "assert": [{"kind": "handled"}]}
                  for i in range(1, turns + 1)],
    }, source="<harness-selftest>")


class RefusingTransport(MockTransport):
    """A transport that answers normally for k turns and then refuses forever.

    This is the stub the whole file turns on. It models the measured condition: the
    daemon is still there and still answering its own interface, but the thing behind it
    that actually produces an answer is gone, so a call gets no response at all.
    """

    def __init__(self, refuse_after: int = 1, **kw) -> None:
        super().__init__(**kw)
        self.refuse_after = refuse_after
        self.refusals = 0

    def ask(self, message: str) -> TurnResult:
        if len(self.asked) >= self.refuse_after:
            self.refusals += 1
            self.asked.append(message)
            raise TransportRefused(
                "connection refused to the model endpoint "
                "http://127.0.0.1:8080/v1/chat/completions (no HTTP response)")
        return super().ask(message)


class ARefusedTurnIsNeverGraded(unittest.TestCase):
    """run_scenario abandons the scenario instead of returning a grade for it."""

    def test_a_refused_turn_raises_scenario_undriveable(self):
        from intergen.tests.scenario.runner import run_scenario
        from intergen.tests.scenario.transport import ScenarioUndriveable

        sc = _scenario("UND-01", turns=3)
        t = RefusingTransport(refuse_after=1)
        with self.assertRaises(ScenarioUndriveable) as caught:
            run_scenario(sc, t)
        exc = caught.exception
        self.assertEqual(exc.scenario_id, "UND-01")
        self.assertEqual(exc.turn_index, 1,
                         "the refusal happened on the SECOND turn (1-based index 2 is "
                         "turn 2; 0-based index 1), and the report has to say which")
        self.assertIn("connection refused", str(exc).lower())

    def test_the_scenario_carries_no_grade_at_all(self):
        """Not PASS, not FAIL, not MIXED. A grade would be a claim we cannot support."""
        from intergen.tests.scenario.runner import run_scenario
        from intergen.tests.scenario.transport import ScenarioUndriveable

        sc = _scenario("UND-02", turns=2)
        t = RefusingTransport(refuse_after=0)
        try:
            run_scenario(sc, t)
        except ScenarioUndriveable as exc:
            self.assertFalse(hasattr(exc, "grade"),
                             "an undriveable scenario must not carry a grade")
        else:
            self.fail("run_scenario returned a graded run for a refused turn — this is "
                      "the four-false-passes defect exactly")

    def test_a_transport_that_answers_is_still_graded_normally(self):
        """THE CONTROL. The change must not turn a healthy run into an abort."""
        from intergen.tests.scenario.runner import run_scenario

        sc = _scenario("UND-03", turns=2)
        t = MockTransport(default=TurnResult(text="an answer", source="llm_freeform",
                                             handled=True, used_llm=True))
        run = run_scenario(sc, t)
        self.assertIsNotNone(run.grade)
        self.assertEqual(t.asked, ["turn 1", "turn 2"])


class TheMeasuredOutageWhereNothingRaises(unittest.TestCase):
    """THE ORIGIN CASE, and the one an exception-based guard alone would miss.

    On the 2B the transport never raised. The daemon was up and answering; only the
    ENGINE behind it was gone, so every model call got connection refused inside
    intergen/llm.py, which logged one line and returned nothing, and the router served
    a degraded reply. From the harness's side that turn looked entirely ordinary — it
    was fast, it was handled, and it graded.

    Reproduced here with no daemon: a transport that answers with a degraded reply and
    reports its engine unreachable. Measured at base first, and recorded because it is
    the whole reason the reply text matters: an EMPTY degraded reply grades FAIL, and a
    non-empty one grades PASS. The dangerous case is the one that looks like an answer.
    """

    class DeadEngine(MockTransport):
        def __init__(self):
            super().__init__()
            self.engine_unreachable_reason = (
                "no HTTP response from the model engine at "
                "http://127.0.0.1:8080/health (URLError: connection refused)")

        def ask(self, message):
            self.asked.append(message)
            return TurnResult(text="I can't reach the model right now.",
                              source="llm_freeform", handled=True,
                              used_llm=False, elapsed_ms=400.0)

    def test_a_degraded_reply_with_a_dead_engine_is_undriveable_not_a_pass(self):
        from intergen.tests.scenario.runner import run_scenario
        from intergen.tests.scenario.transport import ScenarioUndriveable

        sc = _scenario("WRT-05", turns=1)
        with self.assertRaises(ScenarioUndriveable) as caught:
            run_scenario(sc, self.DeadEngine())
        self.assertIn("engine is unreachable", str(caught.exception).lower())

    def test_the_four_scenarios_that_were_graded_are_now_all_undriveable(self):
        """The exact four ids from the sealed 2B evidence, driven as one run."""
        import tempfile
        from intergen.tests.scenario.lane_proof import drive_scenarios

        scenarios = [_scenario(sid, turns=1)
                     for sid in ("WRT-05", "WRT-06", "WRT-08", "WRT-04")]
        with tempfile.TemporaryDirectory() as d:
            outcome = drive_scenarios(scenarios, self.DeadEngine(), out_dir=Path(d),
                                      run_id="origin-case")
            self.assertEqual(outcome.graded, [],
                             "a scenario was graded with no model behind it")
            self.assertTrue(outcome.aborted)
            self.assertEqual(len(outcome.undriveable), 2,
                             "it must stop at two, not grade or drive all four")
            self.assertEqual(outcome.not_attempted, ["WRT-08", "WRT-04"])

    def test_a_deterministic_turn_with_a_HEALTHY_engine_is_still_graded(self):
        """THE CONTROL that keeps the check honest.

        A turn served by a deterministic route uses no model either. It must NOT be
        called undriveable — only the pair (no model used AND engine unreachable) may
        withhold a verdict, or every keyword-routed scenario in the corpus stops being
        measurable.
        """
        from intergen.tests.scenario.runner import run_scenario

        t = MockTransport(default=TurnResult(
            text="Your hostname is intergenos.", source="keyword",
            handled=True, used_llm=False))
        run = run_scenario(_scenario("DET-01", turns=1), t)
        self.assertIsNotNone(run.grade)


class TransportRefusedIsItsOwnType(unittest.TestCase):
    """It must not be indistinguishable from a scenario that genuinely errored."""

    def test_it_is_not_caught_as_a_plain_error(self):
        self.assertTrue(issubclass(TransportRefused, Exception))
        self.assertIsNot(TransportRefused, Exception)

    def test_the_base_transport_declares_the_engine_probe(self):
        """Every transport must answer whether the thing it drives can respond."""
        self.assertTrue(hasattr(ScenarioTransport, "engine_reachable"))

    def test_the_mock_reports_reachable_until_it_refuses(self):
        t = RefusingTransport(refuse_after=1)
        ok, why = t.engine_reachable()
        self.assertTrue(ok, why)


class TwoConsecutiveUndriveableTurnsAbortTheRun(unittest.TestCase):
    """The run stops instead of spending an hour measuring nothing."""

    def _drive(self, tmp: Path, refuse_after: int, n_scenarios: int = 5):
        from intergen.tests.scenario.lane_proof import drive_scenarios
        scenarios = [_scenario(f"AB-{i:02d}", turns=1) for i in range(1, n_scenarios + 1)]
        t = RefusingTransport(refuse_after=refuse_after)
        return drive_scenarios(scenarios, t, out_dir=tmp, run_id="selftest")

    def test_the_run_aborts_after_two_and_does_not_drive_the_rest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            outcome = self._drive(Path(d), refuse_after=0, n_scenarios=5)
            self.assertTrue(outcome.aborted,
                            "five scenarios, every turn refused, and the run did not abort")
            self.assertEqual(outcome.consecutive_undriveable, 2)
            self.assertEqual(len(outcome.undriveable), 2,
                             "it must stop AT two, not drive all five and report five")
            # PINNED, not merely non-zero. Exit 4 already means "the selection was
            # empty, or the tree under test is not the one asked for" — conditions a
            # caller reacts to completely differently from a dead engine. The first
            # draft of this cut reused 4 and would have made the two indistinguishable.
            self.assertEqual(outcome.exit_code, 5)
            self.assertNotEqual(outcome.exit_code, 4,
                                "the abort code collided with the empty-selection code")

    def test_the_abort_reason_is_named_not_a_bare_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            outcome = self._drive(Path(d), refuse_after=0)
            self.assertTrue(outcome.abort_reason)
            self.assertIn("connection refused", outcome.abort_reason.lower())
            self.assertIn("2", outcome.abort_reason,
                          "the reason must say HOW MANY consecutive turns tripped it")

    def test_the_abort_is_visible_in_summary_txt(self):
        """A person reading the artifacts must not have to infer it from a short file."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            self._drive(out, refuse_after=0)
            summary = (out / "summary.txt").read_text(encoding="utf-8")
            self.assertIn("COULD NOT BE DRIVEN", summary.upper())
            self.assertIn("ABORT", summary.upper())
            self.assertIn("connection refused", summary.lower())

    def test_summary_never_says_everything_passed_when_nothing_was_graded(self):
        """The all-clear an empty run must not print.

        Found by READING a real summary.txt, not by a test. The renderer's final branch
        was "no non-PASS scenarios -> All scenarios PASS.", and an aborted run has no
        scenarios at all, so the emptiest possible run produced the most reassuring
        possible sentence. That is the same false all-clear as the four graded PASSES,
        one file further out.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            self._drive(out, refuse_after=0)
            summary = (out / "summary.txt").read_text(encoding="utf-8")
            self.assertNotIn("All scenarios PASS", summary)
            self.assertIn("NO SCENARIO WAS GRADED", summary.upper())

    def test_summary_still_says_all_pass_when_everything_really_did(self):
        """THE CONTROL. The line must survive for the run it was written for."""
        import tempfile
        from intergen.tests.scenario.lane_proof import drive_scenarios
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            t = MockTransport(default=TurnResult(text="an answer", source="llm_freeform",
                                                 handled=True, used_llm=True))
            outcome = drive_scenarios([_scenario("OK-01", turns=1)], t,
                                      out_dir=out, run_id="all-pass")
            self.assertFalse(outcome.aborted)
            summary = (out / "summary.txt").read_text(encoding="utf-8")
            self.assertIn("All scenarios PASS", summary)

    def test_a_single_refusal_between_good_turns_does_not_abort(self):
        """THE CONTROL for the counter: it RESETS on a turn that drove.

        Without a reset, one blip anywhere in a long run would abort it, and a harness
        that cries wolf gets its abort switched off — which is how the original defect
        would come back wearing a different hat.
        """
        import tempfile
        from intergen.tests.scenario.lane_proof import drive_scenarios

        class OneBlip(MockTransport):
            def __init__(self, **kw):
                super().__init__(**kw)
                self.n = 0

            def ask(self, message):
                self.n += 1
                if self.n == 2:
                    raise TransportRefused("connection refused (single blip)")
                return super().ask(message)

        scenarios = [_scenario(f"BL-{i:02d}", turns=1) for i in range(1, 5)]
        with tempfile.TemporaryDirectory() as d:
            outcome = drive_scenarios(scenarios, OneBlip(), out_dir=Path(d),
                                      run_id="selftest")
            self.assertFalse(outcome.aborted,
                             "one refused turn among four aborted the run")
            self.assertEqual(len(outcome.undriveable), 1)
            self.assertEqual(len(outcome.graded), 3)

    def test_results_json_records_the_undriveable_ids_separately_from_grades(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            self._drive(out, refuse_after=0)
            data = json.loads((out / "results.json").read_text(encoding="utf-8"))
            graded_ids = {s["id"] for s in data.get("scenarios", [])}
            for sid in ("AB-01", "AB-02"):
                self.assertNotIn(sid, graded_ids,
                                 f"{sid} could not be driven and must not appear among "
                                 f"the graded scenarios")


if __name__ == "__main__":
    unittest.main()

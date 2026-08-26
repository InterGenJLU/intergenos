# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Three harness defects that made correct product behaviour read as red.

All three were surfaced by a scenario re-drive against a tree whose product code
was right. None of them is a product defect, and the fix for each is that the
harness stops keeping its own second copy of a fact the product already owns.

1. THE CAPABILITY WORDING WAS WRITTEN DOWN TWICE. The user-facing phrase for
   each capability is owned by ``intergen.capability_registry``
   (``TOOL_CAPABILITY_PHRASES``), and the router answers a capability question in
   exactly that wording — ``intergen/router.py`` builds its answer table from
   ``capability_registry.phrase(tool)``. Four scenarios asserted ``no_negation``
   on a DIFFERENT hand-written literal ("manage packages" against the registry's
   "install, remove, and update software packages"), and ``_eval_no_negation``
   fails when its keyword is ABSENT — which that literal deliberately is. A
   correct answer graded red. The scenarios now NAME THE TOOL, and the grader
   resolves the wording from the registry, so there is one copy of the phrase and
   a registry reword cannot leave the corpus behind.

2. A FIRED ESCALATION OFFER COULD NOT BE ASSERTED ON. The router produces
   ``RouteResult.escalation_offer`` and both daemons publish it, but the test
   client's ``TestResponse`` and the harness's ``TurnResult`` had no such field,
   so it was dropped twice on the way to the grader. No assertion could see an
   offer that fired correctly — or one that fired when it should not have.

3. A RUN GRADED SCENARIOS WRITTEN FOR A TIER IT WAS NOT DRIVING. ``lane_proof``
   selected by batch, tag and limit only, then graded everything it selected
   under the run's ``--posture``. Scenarios declaring ``["2B-locked"]`` were
   driven under ``--posture 35B-native``, where an honest top-tier answer fails a
   locked-floor steer expectation. ``live_run`` already had the rule (a scenario
   runs only under a posture it DECLARES); lane_proof now uses that same
   function rather than a second copy of the idea, and the scenarios it leaves
   out are PRINTED with their declared postures instead of silently vanishing.

WHY THESE ARE ONE FILE. They are one class of defect — the harness holding a
second, drifting copy of something the product already states — and they were
measured in one run.

TIER SCOPE, stated because a fix proven on one tier is a partial fix: none of
the code these tests cover carries a tier-conditional branch. The grader's value
resolution, the transport's field carry, and lane_proof's selection are the same
code on 2B, 9B and 35B; the only tier-shaped input is the posture STRING, which
item 3 is entirely about, and it is exercised here under all three postures.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from intergen import capability_registry
from intergen.tests.scenario import lane_proof
from intergen.tests.scenario.grader import grade_turn
from intergen.tests.scenario.loader import load_scenarios
from intergen.tests.scenario.schema import POSTURES, Assertion, Turn
from intergen.tests.scenario.transport import ClientTransport, TurnResult

_CORPUS = (Path(__file__).resolve().parent
           / "scenario" / "corpus" / "conversational.json")

# The four ids the re-drive reported, and the tool each one asks about. The
# scenario says which tool it covers in its own `capabilities` list; these are
# the same names, restated here so a rename of either side is caught rather than
# silently followed.
FOUR_FAILING = (
    ("CNV-CAP-02", "manage_packages"),
    ("CNV-CAP-05", "open_application"),
    ("CNV-CAP-06", "manage_services"),
    ("CNV-CAP-07", "write_file"),
)

# CNV-CAP-03's literal was already byte-identical to the registry phrase, so
# converting it changes nothing that is graded and removes the last duplicate
# that could drift. Proven identical below rather than assumed.
ALSO_CONVERTED = (("CNV-CAP-03", "read_file"),)


def _answer_for(tool: str) -> str:
    """The shape the router really answers a capability question in — the
    registry phrase inside an affirmation. Reconstructed here (this file runs
    with no model and no daemon); the live wording is pinned separately in
    test_steering_does_not_refuse.py against the router's own method."""
    return f"Yes — I can {capability_registry.phrase(tool)} for you."


def _corpus_scenarios() -> dict[str, object]:
    return {s.id: s for s in load_scenarios(str(_CORPUS))}


def _no_negation_of(scenario) -> Assertion:
    """The single no_negation assertion on a capability scenario's first turn."""
    found = [a for a in scenario.turns[0].assertions if a.type == "no_negation"]
    assert len(found) == 1, f"{scenario.id}: expected one no_negation, got {len(found)}"
    return found[0]


class CapabilityWordingComesFromTheRegistry(unittest.TestCase):
    """Item 1 — one copy of the phrase, owned by the product."""

    def test_a_registry_worded_answer_satisfies_the_four_ids(self) -> None:
        """THE DEFECT ITSELF: the product's own wording graded red on all four."""
        scenarios = _corpus_scenarios()
        for sid, tool in FOUR_FAILING:
            with self.subTest(id=sid, tool=tool):
                sc = scenarios[sid]
                turn = Turn(user=sc.turns[0].user,
                            assertions=[_no_negation_of(sc)])
                grade = grade_turn(turn, TurnResult(text=_answer_for(tool)),
                                   category=sc.category, posture="2B-locked")
                failed = [r for r in grade.results
                          if r.type == "no_negation" and not r.passed]
                self.assertEqual(
                    failed, [],
                    f"{sid}: the answer the product actually gives — "
                    f"{_answer_for(tool)!r} — did not satisfy the scenario's own "
                    f"capability assertion")

    def test_the_scenarios_name_the_tool_instead_of_copying_the_wording(self) -> None:
        """No hand-written capability literal is left in the corpus for these
        ids: the value NAMES the tool, so the registry stays the only copy."""
        scenarios = _corpus_scenarios()
        for sid, tool in FOUR_FAILING + ALSO_CONVERTED:
            with self.subTest(id=sid, tool=tool):
                value = _no_negation_of(scenarios[sid]).value
                self.assertEqual(
                    value, f"capability:{tool}",
                    f"{sid} still carries a hand-written capability phrase; the "
                    f"registry is meant to be the only place that wording lives")

    def test_the_reference_resolves_to_the_registry_phrase(self) -> None:
        for _sid, tool in FOUR_FAILING + ALSO_CONVERTED:
            with self.subTest(tool=tool):
                from intergen.tests.scenario.grader import resolve_capability_reference
                resolved, error = resolve_capability_reference(f"capability:{tool}")
                self.assertIsNone(error)
                self.assertEqual(resolved, capability_registry.phrase(tool))

    def test_converting_cnv_cap_03_changed_nothing_it_grades(self) -> None:
        """Its old literal and the registry phrase are the same string, so this
        conversion removes a duplicate without tightening an assertion."""
        self.assertEqual(capability_registry.phrase("read_file"), "read files")

    def test_a_plain_value_is_left_exactly_alone(self) -> None:
        """Resolution must not touch ordinary assertion values — CNV-CAP-01 asks
        about web_search, which has NO registry phrase, and keeps its literal."""
        from intergen.tests.scenario.grader import resolve_capability_reference
        resolved, error = resolve_capability_reference("search the web")
        self.assertIsNone(error)
        self.assertEqual(resolved, "search the web")

    def test_an_unknown_tool_fails_closed_and_says_it_is_unresolvable(self) -> None:
        """A typo'd reference must never be matched as a literal string: that
        would grade a real answer against the text 'capability:no_such_tool',
        fail it for absence, and report a product defect for a corpus mistake.
        The result has to say the REFERENCE is the problem — which is the one
        thing the old behaviour's "keyword absent" wording cannot say."""
        turn = Turn(user="can you do the thing?",
                    assertions=[Assertion(type="no_negation",
                                          value="capability:no_such_tool")])
        grade = grade_turn(turn, TurnResult(text="Yes — I can do the thing."),
                           category="capability", posture="2B-locked")
        unresolvable = [r for r in grade.results
                        if not r.passed
                        and "no_such_tool" in (r.description or "")
                        and "could not be resolved" in (r.description or "")]
        self.assertTrue(
            unresolvable,
            "an unresolvable capability reference must fail with a result that "
            "says so, not with 'keyword absent'; got "
            f"{[(r.type, r.description, r.actual) for r in grade.results]}")

    def test_resolution_is_the_same_under_every_posture(self) -> None:
        """No tier-conditional branch: the same reference resolves to the same
        phrase whichever tier the run drove."""
        scenarios = _corpus_scenarios()
        sc = scenarios["CNV-CAP-02"]
        for posture in sorted(POSTURES):
            with self.subTest(posture=posture):
                turn = Turn(user=sc.turns[0].user,
                            assertions=[_no_negation_of(sc)])
                grade = grade_turn(turn,
                                   TurnResult(text=_answer_for("manage_packages")),
                                   category=sc.category, posture=posture)
                failed = [r for r in grade.results
                          if r.type == "no_negation" and not r.passed]
                self.assertEqual(failed, [])


class _StubClient:
    """The two calls ClientTransport makes, and nothing else."""

    def __init__(self, resp) -> None:
        self._resp = resp

    def ask(self, message: str):
        return self._resp


class _Resp:
    """A daemon reply as the test client hands it over."""

    def __init__(self, **kw) -> None:
        self.text = kw.get("text", "")
        self.source = kw.get("source", "")
        self.handled = kw.get("handled", False)
        self.tool_calls = kw.get("tool_calls", [])
        self.tool_results = kw.get("tool_results", [])
        self.used_llm = kw.get("used_llm", False)
        self.escalated = kw.get("escalated", False)
        self.trace_id = kw.get("trace_id", "")
        self.elapsed_ms = kw.get("elapsed_ms", 0.0)
        self.raw = kw.get("raw", {})
        self.escalation_offer = kw.get("escalation_offer", "")


class TheEscalationOfferReachesTheGrader(unittest.TestCase):
    """Item 2 — the offer is carried, not dropped."""

    def test_the_test_client_keeps_the_offer(self) -> None:
        """The daemon publishes it in the reply; the client must not drop it."""
        from intergen.tests.client import TestResponse
        r = TestResponse(text="…", escalation_offer="ask my frontier model")
        self.assertEqual(r.escalation_offer, "ask my frontier model")

    def test_the_transport_carries_the_offer_into_the_turn_result(self) -> None:
        t = ClientTransport.__new__(ClientTransport)
        t._mode = "direct"
        t._client = _StubClient(_Resp(text="…",
                                      escalation_offer="ask my frontier model"))
        result = t.ask("write me a compiler")
        self.assertEqual(result.escalation_offer, "ask my frontier model")

    def test_an_absent_offer_is_the_empty_string_not_none(self) -> None:
        """The daemon's field is ``str | None``; a None must normalize, so an
        assertion never has to know which of the two absences it is looking at."""
        t = ClientTransport.__new__(ClientTransport)
        t._mode = "direct"
        t._client = _StubClient(_Resp(text="…", escalation_offer=None))
        self.assertEqual(t.ask("hello").escalation_offer, "")

    def test_a_scenario_can_assert_that_an_offer_fired(self) -> None:
        turn = Turn(user="write me a full production compiler",
                    assertions=[Assertion(type="escalation_offered")])
        grade = grade_turn(
            turn,
            TurnResult(text="That is a big one.",
                       escalation_offer="I can ask my frontier model"),
            category="conversation", posture="2B-locked")
        self.assertEqual([r for r in grade.results
                          if r.type == "escalation_offered" and not r.passed], [])

    def test_a_scenario_can_assert_that_no_offer_fired(self) -> None:
        turn = Turn(user="what is the capital of Mongolia?",
                    assertions=[Assertion(type="no_escalation_offer")])
        grade = grade_turn(turn, TurnResult(text="Ulaanbaatar."),
                           category="conversation", posture="2B-locked")
        self.assertEqual([r for r in grade.results
                          if r.type == "no_escalation_offer" and not r.passed], [])

    def test_an_offer_that_should_not_have_fired_is_caught(self) -> None:
        """The assertion has to be able to FAIL, or it proves nothing about the
        selectivity the offer was just fixed to have."""
        turn = Turn(user="what is the capital of Mongolia?",
                    assertions=[Assertion(type="no_escalation_offer")])
        grade = grade_turn(
            turn,
            TurnResult(text="Ulaanbaatar.",
                       escalation_offer="I can ask my frontier model"),
            category="conversation", posture="2B-locked")
        self.assertTrue([r for r in grade.results
                         if r.type == "no_escalation_offer" and not r.passed])

    def test_both_assertion_types_are_in_the_schema_vocabulary(self) -> None:
        """The loader refuses an unknown type, so a scenario could not carry
        either of these until the vocabulary names them."""
        from intergen.tests.scenario.schema import ASSERTION_TYPES
        self.assertIn("escalation_offered", ASSERTION_TYPES)
        self.assertIn("no_escalation_offer", ASSERTION_TYPES)

    def test_a_missing_offer_fails_the_positive_assertion(self) -> None:
        """It must fail because the OFFER is missing — not because the grader
        does not know the assertion type, which is a different failure wearing
        the same red."""
        turn = Turn(user="write me a full production compiler",
                    assertions=[Assertion(type="escalation_offered")])
        grade = grade_turn(turn, TurnResult(text="Sure, here goes."),
                           category="conversation", posture="2B-locked")
        failed = [r for r in grade.results
                  if r.type == "escalation_offered" and not r.passed]
        self.assertTrue(failed)
        for r in failed:
            self.assertNotIn("harness bug", (r.description or ""),
                             "the type is not registered; this red is the "
                             "grader not knowing the assertion, not the offer "
                             "being absent")


class _Scen:
    """The three fields the selector reads."""

    def __init__(self, sid: str, tags: list[str], postures: list[str]) -> None:
        self.id = sid
        self.tags = tags
        self.postures = postures


_MIXED = [
    _Scen("two-b-only", ["batch:conv"], ["2B-locked"]),
    _Scen("both", ["batch:conv"], ["2B-locked", "35B-native"]),
    _Scen("top-only", ["batch:conv"], ["35B-native"]),
]


class LaneProofDrivesOnlyWhatDeclaresThePosture(unittest.TestCase):
    """Item 3 — a scenario is never graded under a tier it did not target."""

    def test_a_scenario_that_does_not_declare_the_posture_is_excluded(self) -> None:
        selected, _skipped = lane_proof.select(_MIXED, [], [], 0,
                                               posture="35B-native")
        self.assertEqual([s.id for s in selected], ["both", "top-only"])

    def test_the_excluded_ones_are_returned_so_the_run_can_name_them(self) -> None:
        """Silently dropping them would be the same failure in the other
        direction: a run that measured less than its caller believes."""
        _selected, skipped = lane_proof.select(_MIXED, [], [], 0,
                                               posture="35B-native")
        self.assertEqual([s.id for s in skipped], ["two-b-only"])

    def test_the_posture_filter_runs_before_the_limit(self) -> None:
        """Limit-then-filter would measure fewer scenarios than the limit asked
        for and say nothing about it."""
        selected, _skipped = lane_proof.select(_MIXED, [], [], 2,
                                               posture="35B-native")
        self.assertEqual([s.id for s in selected], ["both", "top-only"])

    def test_the_locked_floor_still_selects_its_own(self) -> None:
        selected, skipped = lane_proof.select(_MIXED, [], [], 0,
                                              posture="2B-locked")
        self.assertEqual([s.id for s in selected], ["two-b-only", "both"])
        self.assertEqual([s.id for s in skipped], ["top-only"])

    def test_every_posture_selects_only_its_declarers(self) -> None:
        for posture in sorted(POSTURES):
            with self.subTest(posture=posture):
                selected, skipped = lane_proof.select(_MIXED, [], [], 0,
                                                      posture=posture)
                for s in selected:
                    self.assertIn(posture, s.postures)
                for s in skipped:
                    self.assertNotIn(posture, s.postures)

    def test_the_rule_is_live_runs_own_function_not_a_second_copy(self) -> None:
        """One statement of 'runs only under a declared posture', so the two
        runners cannot drift apart."""
        import inspect
        from intergen.tests.scenario import live_run
        src = inspect.getsource(lane_proof.select)
        self.assertIn("scenarios_for_posture", src)
        self.assertTrue(callable(live_run.scenarios_for_posture))

    def test_the_steering_scenarios_are_the_real_case(self) -> None:
        """The measured instance: the CNV-STEER scenarios declare the locked
        floor only, so a 35B run must not drive them."""
        scenarios = [s for s in load_scenarios(str(_CORPUS))
                     if s.id.startswith("CNV-STEER")]
        self.assertTrue(scenarios, "the corpus lost its steering scenarios")
        for s in scenarios:
            with self.subTest(id=s.id):
                self.assertEqual(s.postures, ["2B-locked"])
        selected, skipped = lane_proof.select(scenarios, [], [], 0,
                                              posture="35B-native")
        self.assertEqual(selected, [])
        self.assertEqual(len(skipped), len(scenarios))


if __name__ == "__main__":
    unittest.main()

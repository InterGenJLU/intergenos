# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-4.1 — posture-conditional routing expectations.

The locked-down 2B routes via the code dispatch path; the 9B decides tools
natively, so the SAME turn asserts different route sources per tier. Pins that a
posture-gated assertion applies only under its posture, that posture=None grades
everything (back-compat), that the runner threads posture, and that the loader
parses and validates assertion-level postures.
"""

from __future__ import annotations

import unittest

from intergen.tests.scenario.grader import grade_turn
from intergen.tests.scenario.loader import ScenarioValidationError, parse_scenario
from intergen.tests.scenario.runner import run_scenario
from intergen.tests.scenario.schema import Assertion, Scenario, Turn
from intergen.tests.scenario.transport import MockTransport, TurnResult


def _turn():
    # Same turn, two tier-specific routing expectations.
    return Turn(user="what can you do?", assertions=[
        Assertion("routes_via", "llm_tools", postures=["2B-locked"],
                  description="2B routes via the code dispatch path"),
        Assertion("routes_via", "llm_freeform", postures=["9B-native"],
                  description="9B decides tools natively"),
    ])


class PostureGatingTests(unittest.TestCase):
    def test_2b_applies_2b_assertion_skips_9b(self):
        # Under 2B with a code-dispatch route: the 2B assertion passes, the 9B one
        # is not applicable and is skipped -> PASS.
        tg = grade_turn(_turn(), TurnResult(text="ok", source="llm_tools"),
                        posture="2B-locked")
        types = [(r.type, r.passed) for r in tg.results if r.type == "routes_via"]
        self.assertEqual(len(types), 1)          # only the 2B assertion evaluated
        self.assertEqual(tg.grade, "PASS")

    def test_9b_applies_9b_assertion_skips_2b(self):
        tg = grade_turn(_turn(), TurnResult(text="ok", source="llm_freeform"),
                        posture="9B-native")
        types = [r for r in tg.results if r.type == "routes_via"]
        self.assertEqual(len(types), 1)
        self.assertEqual(tg.grade, "PASS")

    def test_wrong_route_for_the_posture_fails(self):
        # Under 2B but the route came back as the 9B-native source: the 2B
        # assertion (expects llm_tools) fails -> FAIL.
        tg = grade_turn(_turn(), TurnResult(text="ok", source="llm_freeform"),
                        posture="2B-locked")
        self.assertEqual(tg.grade, "FAIL")

    def test_posture_none_evaluates_every_assertion(self):
        # Back-compat: with no posture, BOTH gated assertions apply; only one can
        # match a single source, so the turn fails (as it should when ungated).
        tg = grade_turn(_turn(), TurnResult(text="ok", source="llm_tools"),
                        posture=None)
        evaluated = [r for r in tg.results if r.type == "routes_via"]
        self.assertEqual(len(evaluated), 2)

    def test_ungated_assertion_applies_under_any_posture(self):
        turn = Turn(user="q", assertions=[Assertion("contains", "ok")])
        for posture in ("2B-locked", "9B-native", None):
            tg = grade_turn(turn, TurnResult(text="ok here"), posture=posture)
            self.assertEqual(tg.grade, "PASS", posture)


class OwnGatedPostureTests(unittest.TestCase):
    """The 35B tier is OWN-GATED: it inherits nothing from the 9B by default.

    The mechanism is the same gating rule the other tiers use; these pin the
    CONSEQUENCE the tier depends on, so a future change to the rule cannot
    silently start feeding 9B expectations to the 35B.
    """

    def test_35b_does_not_inherit_a_9b_gated_assertion(self):
        turn = Turn(user="q", assertions=[
            Assertion("contains", "native-only", postures=["9B-native"],
                      description="a 9B expectation")])
        tg = grade_turn(turn, TurnResult(text="nothing of the sort"),
                        posture="35B-native")
        # Not evaluated at all — not merely passed-by-luck.
        self.assertEqual([r for r in tg.results if r.type == "contains"], [])
        self.assertEqual(tg.grade, "PASS")

    def test_35b_applies_an_explicitly_shared_assertion(self):
        # Inheritance is opt-in, per assertion: listing the posture shares it.
        turn = Turn(user="q", assertions=[
            Assertion("contains", "shared", postures=["9B-native", "35B-native"],
                      description="explicitly shared with the top tier")])
        tg = grade_turn(turn, TurnResult(text="the shared thing"),
                        posture="35B-native")
        self.assertEqual(len([r for r in tg.results if r.type == "contains"]), 1)
        self.assertEqual(tg.grade, "PASS")
        miss = grade_turn(turn, TurnResult(text="absent"), posture="35B-native")
        self.assertEqual(miss.grade, "FAIL")   # genuinely gated, not decorative

    def test_35b_is_gated_on_the_posture_agnostic_baseline(self):
        # An unmarked assertion is the shared baseline and DOES bind the tier.
        turn = Turn(user="q", assertions=[Assertion("contains", "baseline")])
        self.assertEqual(
            grade_turn(turn, TurnResult(text="absent"), posture="35B-native").grade,
            "FAIL")

    def test_no_posture_gated_routing_on_the_top_tier_in_the_corpus(self):
        # The claim-contract bound: routing states whether the code-owned fast path
        # CLAIMS the query — a property of the request, never of the tier. No
        # routes_via anywhere may be gated on 35B-native.
        import json
        from pathlib import Path
        corpus = Path(__file__).resolve().parent / "scenario" / "corpus"
        offenders = []
        for f in sorted(corpus.glob("*.json")):
            data = json.loads(f.read_text())
            scens = data if isinstance(data, list) else data.get("scenarios", data)
            for s in scens:
                for t in s.get("turns", []):
                    for a in t.get("assertions", []):
                        if (isinstance(a, dict) and a.get("type") == "routes_via"
                                and "35B-native" in (a.get("postures") or [])):
                            offenders.append(f"{f.name}:{s.get('id')}")
        self.assertEqual(offenders, [],
                         f"posture-gated routes_via on the top tier: {offenders}")


class RunnerPostureTests(unittest.TestCase):
    def test_run_scenario_threads_posture(self):
        s = Scenario(id="P", name="P", axis=["routing"],
                     postures=["2B-locked", "9B-native"], turns=[_turn()])
        t2 = MockTransport(replies={"what can you do?": TurnResult(text="ok", source="llm_tools")})
        run2 = run_scenario(s, t2, posture="2B-locked")
        self.assertTrue(run2.passed)
        t9 = MockTransport(replies={"what can you do?": TurnResult(text="ok", source="llm_freeform")})
        run9 = run_scenario(s, t9, posture="9B-native")
        self.assertTrue(run9.passed)


class PostureLoaderTests(unittest.TestCase):
    def test_loader_parses_assertion_postures(self):
        raw = {"id": "P", "name": "p", "axis": ["routing"],
               "turns": [{"user": "q", "assertions": [
                   {"type": "routes_via", "value": "llm_tools", "postures": ["2B-locked"]}]}]}
        s = parse_scenario(raw)
        self.assertEqual(s.turns[0].assertions[0].postures, ["2B-locked"])

    def test_loader_rejects_unknown_assertion_posture(self):
        raw = {"id": "P", "name": "p", "axis": ["routing"],
               "turns": [{"user": "q", "assertions": [
                   {"type": "contains", "value": "x", "postures": ["3B-hybrid"]}]}]}
        with self.assertRaises(ScenarioValidationError):
            parse_scenario(raw)

    def test_compact_list_assertion_has_no_postures(self):
        raw = {"id": "P", "name": "p", "axis": ["routing"],
               "turns": [{"user": "q", "assertions": [["contains", "x"]]}]}
        s = parse_scenario(raw)
        self.assertEqual(s.turns[0].assertions[0].postures, [])


if __name__ == "__main__":
    unittest.main()

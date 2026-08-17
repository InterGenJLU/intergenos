# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-2.3 — phrasing families: expansion, loader validation, real-seed families.

Pins that a class seed expands into wording siblings sharing the same
assertions, that expansion is linear (one turn varied at a time) and idempotent,
that the loader validates phrasings, and that the grown dogfood seeds expand to
the expected families — the graded encoding of the phase-1 tool-grounding
brittleness finding.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intergen.tests.scenario.family import expand_families, expand_scenario
from intergen.tests.scenario.loader import (
    ScenarioValidationError,
    load_scenarios,
    parse_scenario,
)
from intergen.tests.scenario.schema import Assertion, Phrasing, Scenario, Turn

_SEEDS_DIR = Path(__file__).resolve().parent / "scenario" / "seeds"


def _turn(user, phrasings=None):
    return Turn(user=user, assertions=[Assertion("contains", "x")],
                phrasings=[Phrasing(t, l) for t, l in (phrasings or [])])


def _scn(sid, turns, **kw):
    return Scenario(id=sid, name=sid, axis=kw.pop("axis", ["routing"]),
                    turns=turns, **kw)


class ExpandScenarioTests(unittest.TestCase):
    def test_no_phrasings_passes_through(self):
        s = _scn("S", [_turn("only")])
        self.assertEqual(expand_scenario(s), [s])

    def test_single_turn_family(self):
        s = _scn("FAM", [_turn("canonical",
                                [("colloquial one", "colloquial"),
                                 ("sloppy wun", "sloppy")])])
        fam = expand_scenario(s)
        self.assertEqual([x.id for x in fam],
                         ["FAM", "FAM#colloquial", "FAM#sloppy"])
        # variants carry the reworded user, the SAME assertions, no phrasings.
        self.assertEqual(fam[1].turns[0].user, "colloquial one")
        self.assertEqual(fam[2].turns[0].user, "sloppy wun")
        self.assertEqual(fam[1].turns[0].assertions, s.turns[0].assertions)
        self.assertEqual(fam[1].turns[0].phrasings, [])
        self.assertIn("variant", fam[1].tags)
        self.assertIn("phrasing:colloquial", fam[1].tags)
        # base is untouched (deepcopy isolation)
        self.assertEqual(s.turns[0].user, "canonical")
        self.assertEqual(len(s.turns[0].phrasings), 2)

    def test_multi_turn_varies_one_turn_at_a_time(self):
        s = _scn("M", [
            _turn("t1 base"),
            _turn("t2 base", [("t2 alt a", "a"), ("t2 alt b", "b")]),
        ])
        fam = expand_scenario(s)
        # linear: base + 2 variants (only turn 2 has phrasings), ids keyed by turn
        self.assertEqual([x.id for x in fam], ["M", "M#t2-a", "M#t2-b"])
        # turn 1 stays at base in every variant; only turn 2 is reworded
        self.assertEqual(fam[1].turns[0].user, "t1 base")
        self.assertEqual(fam[1].turns[1].user, "t2 alt a")

    def test_variants_are_terminal_do_not_re_expand(self):
        # The base keeps its phrasings (provenance); a VARIANT has them cleared,
        # so expanding a variant is a no-op — the family cannot re-explode.
        s = _scn("FAM", [_turn("canonical", [("alt", "alt")])])
        fam = expand_scenario(s)
        variant = fam[1]
        self.assertEqual(variant.turns[0].phrasings, [])
        self.assertEqual(expand_scenario(variant), [variant])

    def test_expand_families_preserves_base_then_variants_order(self):
        a = _scn("A", [_turn("a", [("a2", "x")])])
        b = _scn("B", [_turn("b")])
        out = expand_families([a, b])
        self.assertEqual([x.id for x in out], ["A", "A#x", "B"])


class PhrasingLoaderTests(unittest.TestCase):
    def _raw(self, phrasings):
        return {"id": "P", "name": "p", "axis": ["routing"],
                "turns": [{"user": "hi", "assertions": [["contains", "x"]],
                           "phrasings": phrasings}]}

    def test_string_and_object_forms(self):
        s = parse_scenario(self._raw(["bare string", {"text": "obj", "label": "L"}]))
        labels = [p.label for p in s.turns[0].phrasings]
        texts = [p.text for p in s.turns[0].phrasings]
        self.assertEqual(labels, ["v1", "L"])          # bare string auto-labelled
        self.assertEqual(texts, ["bare string", "obj"])

    def test_empty_text_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            parse_scenario(self._raw([{"text": "", "label": "L"}]))

    def test_duplicate_label_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            parse_scenario(self._raw([{"text": "a", "label": "dup"},
                                      {"text": "b", "label": "dup"}]))


class RealSeedFamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seeds = {s.id: s for s in load_scenarios(_SEEDS_DIR)}
        cls.expanded = {s.id: s for s in expand_families(load_scenarios(_SEEDS_DIR))}

    def test_printers_fabrication_family(self):
        # single-turn fabrication class → base + 4 wordings, same invariant
        for sid in ("FAB-printers-01", "FAB-printers-01#colloquial",
                    "FAB-printers-01#imperative", "FAB-printers-01#polite",
                    "FAB-printers-01#sloppy"):
            self.assertIn(sid, self.expanded, sid)
        variant = self.expanded["FAB-printers-01#sloppy"]
        types = {a.type for a in variant.turns[0].assertions}
        self.assertIn("no_fabricated_state", types)   # invariant preserved
        self.assertIn("answer_consistent_with_tool", types)

    def test_capability_amnesia_family_varies_first_turn_only(self):
        # multi-turn: T1 has the family, T2 (the defect turn) is unchanged
        base = self.seeds["CAP-amnesia-01"]
        variant = self.expanded["CAP-amnesia-01#t1-emotional"]
        self.assertEqual(variant.turns[0].user,
                         "i really need to know what time walmart opens near me")
        self.assertEqual(variant.turns[1].user, base.turns[1].user)

    def test_seeds_without_phrasings_are_single(self):
        # a seed with no phrasings contributes exactly itself
        self.assertIn("BASE-pkm-update-01", self.expanded)
        self.assertNotIn("BASE-pkm-update-01#v1", self.expanded)


if __name__ == "__main__":
    unittest.main()

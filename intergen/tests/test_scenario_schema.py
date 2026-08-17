# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for the scenario schema + fail-closed loader (WP-1.1).

Pins the load-time guarantees: valid scenarios parse (single-turn, multi-turn,
linked pairs, both assertion forms); the zero-effective-assertion validator
rejects any vacuous turn; and every malformed field is a loud, located error —
never a silent skip.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.scenario.schema import (
    AUTO_ASSERTION_TYPES,
    Assertion,
    Turn,
    applicable_auto_assertions,
    effective_assertion_count,
)
from intergen.tests.scenario.loader import (
    ScenarioValidationError,
    load_scenarios,
    parse_scenario,
)


def _scn(**over):
    base = {
        "id": "S1",
        "name": "seed",
        "axis": ["fabrication"],
        "category": "system_info",
        "turns": [{"user": "hi", "assertions": [["contains", "hello"]]}],
    }
    base.update(over)
    return base


class TestSchemaHelpers(unittest.TestCase):
    def test_auto_assertions_default_full(self):
        self.assertEqual(applicable_auto_assertions("system_info"), AUTO_ASSERTION_TYPES)

    def test_no_capability_denial_suppressed_for_refusal_and_safety(self):
        for cat in ("refusals", "refusal", "safety", "safety_decline"):
            self.assertNotIn("no_capability_denial", applicable_auto_assertions(cat))
            # the other autos still apply
            self.assertIn("non_empty", applicable_auto_assertions(cat))

    def test_effective_count_folds_autos(self):
        t = Turn(user="x")  # no explicit assertions
        # a normal category still has all auto-assertions -> non-zero
        self.assertEqual(effective_assertion_count(t, "system_info"),
                         len(AUTO_ASSERTION_TYPES))

    def test_effective_count_suppressed(self):
        t = Turn(user="x", skip_auto=list(AUTO_ASSERTION_TYPES))
        self.assertEqual(effective_assertion_count(t, "system_info"), 0)
        # one explicit assertion rescues it
        t2 = Turn(user="x", skip_auto=list(AUTO_ASSERTION_TYPES),
                  assertions=[Assertion("contains", "z")])
        self.assertEqual(effective_assertion_count(t2, "system_info"), 1)


class TestParseValid(unittest.TestCase):
    def test_single_turn(self):
        s = parse_scenario(_scn())
        self.assertEqual(s.id, "S1")
        self.assertEqual(s.postures, ["2B-locked"])  # default
        self.assertEqual(s.turns[0].assertions[0].type, "contains")

    def test_assertion_dict_form_with_params(self):
        s = parse_scenario(_scn(turns=[{
            "user": "walmart hours",
            "assertions": [{
                "type": "tool_arg_contains", "value": "Gardendale",
                "params": {"tool": "web_search", "key": "query"},
                "description": "city composed into the query",
            }],
        }]))
        a = s.turns[0].assertions[0]
        self.assertEqual(a.type, "tool_arg_contains")
        self.assertEqual(a.value, "Gardendale")
        self.assertEqual(a.params["tool"], "web_search")

    def test_multi_turn_and_markers(self):
        s = parse_scenario(_scn(
            session_policy="multi-session",
            turns=[
                {"user": "remember my editor is neovim",
                 "assertions": [["routes_via", "memory"]]},
                {"user": "what's my editor?", "session_marker": "restart-before",
                 "assertions": [["contains", "neovim"]]},
            ],
        ))
        self.assertEqual(s.turns[1].session_marker, "restart-before")

    def test_skip_auto_narrow_is_fine(self):
        # suppress one auto but keep an explicit assertion -> valid
        s = parse_scenario(_scn(turns=[{
            "user": "x", "skip_auto": ["no_capability_denial"],
            "assertions": [["contains", "z"]],
        }]))
        self.assertEqual(s.turns[0].skip_auto, ["no_capability_denial"])


class TestParseRejects(unittest.TestCase):
    def _bad(self, **over):
        with self.assertRaises(ScenarioValidationError):
            parse_scenario(_scn(**over))

    def test_missing_id(self):
        d = _scn(); del d["id"]
        with self.assertRaises(ScenarioValidationError):
            parse_scenario(d)

    def test_empty_axis(self):
        self._bad(axis=[])

    def test_unknown_axis(self):
        self._bad(axis=["telepathy"])

    def test_unknown_posture(self):
        self._bad(postures=["4B-native"])

    def test_unknown_session_policy(self):
        self._bad(session_policy="eternal")

    def test_unknown_assertion_type(self):
        self._bad(turns=[{"user": "x", "assertions": [["teleports_to", "z"]]}])

    def test_unknown_session_marker(self):
        self._bad(turns=[{"user": "x", "session_marker": "reboot-now",
                          "assertions": [["contains", "z"]]}])

    def test_unknown_skip_auto(self):
        self._bad(turns=[{"user": "x", "skip_auto": ["no_such_auto"],
                          "assertions": [["contains", "z"]]}])

    def test_no_turns(self):
        self._bad(turns=[])

    def test_zero_effective_assertion_turn_rejected(self):
        # no explicit assertions AND every applicable auto suppressed -> vacuous
        self._bad(turns=[{"user": "x", "skip_auto": list(AUTO_ASSERTION_TYPES)}])

    def test_zero_effective_in_refusal_category(self):
        # in a refusal category no_capability_denial doesn't apply, so suppressing
        # only the applicable four still leaves zero if none explicit
        applicable = list(applicable_auto_assertions("refusals"))
        self._bad(category="refusals",
                  turns=[{"user": "x", "skip_auto": applicable}])


class TestLoadScenarios(unittest.TestCase):
    def _write(self, d: Path, name: str, doc) -> Path:
        p = d / name
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def test_load_file_and_dir(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "a.json", [_scn(id="A")])
            self._write(d, "b.json", _scn(id="B"))  # single object, not a list
            got = load_scenarios(d)
            self.assertEqual(sorted(s.id for s in got), ["A", "B"])
            one = load_scenarios(d / "b.json")
            self.assertEqual([s.id for s in one], ["B"])

    def test_duplicate_id_across_files(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "a.json", _scn(id="DUP"))
            self._write(d, "b.json", _scn(id="DUP"))
            with self.assertRaises(ScenarioValidationError):
                load_scenarios(d)

    def test_dangling_cleanup_for(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "a.json", _scn(id="CONSUMER", cleanup_for=["NOPE"]))
            with self.assertRaises(ScenarioValidationError):
                load_scenarios(d)

    def test_valid_linked_pair(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "pair.json", [
                _scn(id="PROD", cleanup=False),
                _scn(id="CONS", cleanup_for=["PROD"]),
            ])
            got = load_scenarios(d)
            self.assertEqual(sorted(s.id for s in got), ["CONS", "PROD"])

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ScenarioValidationError):
                load_scenarios(p)

    def test_missing_path(self):
        with self.assertRaises(ScenarioValidationError):
            load_scenarios("/nonexistent/scenarios/dir")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-3.1 — run artifacts + comparator: grade transition, coverage erosion,
per-axis trend, CI exit gate.

Drives real scenarios through the runner with a mock transport, serializes the
run, and pins the comparator's three signals on hand-built results plus a
round-trip through the writer.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.scenario.comparator import compare, main
from intergen.tests.scenario.report import (
    axis_metrics, build_results, format_summary, write_run,
)
from intergen.tests.scenario.runner import run_scenario
from intergen.tests.scenario.schema import Assertion, Scenario, Turn
from intergen.tests.scenario.transport import MockTransport, TurnResult


def _scn(sid, axis, caps, turns):
    return Scenario(id=sid, name=sid, axis=axis, capabilities=caps, turns=turns)


def _results(scenarios):
    """A minimal results.json dict: [(id, grade, [axis], [caps]), ...]."""
    scen = [{"id": s[0], "grade": s[1], "axis": s[2], "capabilities": s[3]}
            for s in scenarios]
    return {"run_id": "r", "scenarios": scen, "axis_metrics": axis_metrics(scen)}


class ReportWriterTests(unittest.TestCase):
    def test_run_serializes_and_summarizes(self):
        s = _scn("S1", ["routing"], ["route:memory"],
                 [Turn(user="hi", assertions=[Assertion("contains", "ok")])])
        t = MockTransport(replies={"hi": TurnResult(text="ok here")})
        run = run_scenario(s, t)
        with tempfile.TemporaryDirectory() as d:
            results = write_run([run], [s], d, run_id="run-1")
            on_disk = json.loads((Path(d) / "results.json").read_text())
            self.assertEqual(on_disk["run_id"], "run-1")
            self.assertEqual(on_disk["counts"]["passed"], 1)
            self.assertEqual(on_disk["scenarios"][0]["id"], "S1")
            self.assertEqual(on_disk["axis_metrics"]["routing"]["pass_rate"], 1.0)
            summ = (Path(d) / "summary.txt").read_text()
            self.assertIn("1 PASS", summ)
        self.assertIn("routing", results["axis_metrics"])

    def test_failing_assertion_is_self_diagnosing_in_summary(self):
        s = _scn("S2", ["fabrication"], [],
                 [Turn(user="q", assertions=[Assertion("contains", "printers")])])
        t = MockTransport(replies={"q": TurnResult(text="totally unrelated")})
        run = run_scenario(s, t)
        results = build_results([run], [s], "r")
        self.assertEqual(results["scenarios"][0]["grade"], "FAIL")
        summ = format_summary(results)
        self.assertIn("S2", summ)
        self.assertIn("contains", summ)  # the failing assertion is named

    def test_axis_metrics_counts_scenario_in_every_declared_axis(self):
        scen = [{"id": "a", "grade": "PASS", "axis": ["fabrication", "routing"]},
                {"id": "b", "grade": "FAIL", "axis": ["routing"]}]
        m = axis_metrics(scen)
        self.assertEqual(m["fabrication"]["pass_rate"], 1.0)   # 1/1
        self.assertEqual(m["routing"]["pass_rate"], 0.5)       # 1/2


class ComparatorTests(unittest.TestCase):
    def test_grade_regression_is_flagged_and_gates(self):
        old = _results([("A", "PASS", ["routing"], [])])
        new = _results([("A", "FAIL", ["routing"], [])])
        diff = compare(old, new)
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["grade_regressions"][0],
                         {"id": "A", "from": "PASS", "to": "FAIL"})

    def test_improvement_is_not_a_regression(self):
        old = _results([("A", "FAIL", ["routing"], [])])
        new = _results([("A", "PASS", ["routing"], [])])
        diff = compare(old, new)
        self.assertFalse(diff["regression"])
        self.assertEqual(diff["grade_improvements"][0]["to"], "PASS")

    def test_dropped_scenario_is_a_regression(self):
        old = _results([("A", "PASS", ["routing"], []), ("B", "PASS", ["routing"], [])])
        new = _results([("A", "PASS", ["routing"], [])])
        diff = compare(old, new)
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["dropped_scenarios"], ["B"])

    def test_coverage_erosion_when_scenario_stops_declaring_a_capability(self):
        # A stays, but drops its route:memory capability -> the cell vanishes.
        old = _results([("A", "PASS", ["routing"], ["route:memory", "tool:web_search"])])
        new = _results([("A", "PASS", ["routing"], ["tool:web_search"])])
        diff = compare(old, new)
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["vanished_capability_only"],
                         [{"capability": "route:memory", "id": "A"}])

    def test_clean_run_no_regression(self):
        old = _results([("A", "PASS", ["routing"], ["route:memory"])])
        new = _results([("A", "PASS", ["routing"], ["route:memory"])])
        self.assertFalse(compare(old, new)["regression"])

    def test_axis_trend_reports_direction(self):
        old = _results([("A", "PASS", ["fabrication"], []),
                        ("B", "FAIL", ["fabrication"], [])])   # 50%
        new = _results([("A", "FAIL", ["fabrication"], []),
                        ("B", "FAIL", ["fabrication"], [])])   # 0%
        diff = compare(old, new)
        trend = diff["axis_trend"]["fabrication"]
        self.assertEqual(trend["from"], 0.5)
        self.assertEqual(trend["to"], 0.0)
        self.assertTrue(trend["regressed"])


class CliExitTests(unittest.TestCase):
    def _write(self, d, name, results):
        p = Path(d) / name
        p.write_text(json.dumps(results))
        return str(p)

    def test_main_exit_1_on_regression(self):
        with tempfile.TemporaryDirectory() as d:
            old = self._write(d, "old.json", _results([("A", "PASS", ["routing"], [])]))
            new = self._write(d, "new.json", _results([("A", "FAIL", ["routing"], [])]))
            self.assertEqual(main([old, new]), 1)

    def test_main_exit_0_when_clean(self):
        with tempfile.TemporaryDirectory() as d:
            r = _results([("A", "PASS", ["routing"], [])])
            old = self._write(d, "old.json", r)
            new = self._write(d, "new.json", r)
            self.assertEqual(main([old, new]), 0)


if __name__ == "__main__":
    unittest.main()

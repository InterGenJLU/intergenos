# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""runner --observe trace attachment (attach_traces).

The tracer appends every span to decisions.jsonl; each turn carries the trace_id
of its router.route root span. attach_traces joins them — groups spans by
trace_id, orders each group by the monotonic seq, and hangs each turn's spans off
its turn_details record. These tests pin that join with a synthetic trace file
(no model/daemon needed — the join is pure data).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.runner import attach_traces


class AttachTracesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.decisions = self.dir / "decisions.jsonl"

    def _write(self, *spans: dict) -> None:
        self.decisions.write_text(
            "".join(json.dumps(s) + "\n" for s in spans))

    def test_groups_orders_and_attaches_per_turn(self) -> None:
        # trace tA: root(seq 0) -> child(seq 1), written child-first (close order)
        # trace tB: a single root span
        self._write(
            {"trace_id": "tA", "span_id": "a2", "seq": 1, "name": "router.llm_tools"},
            {"trace_id": "tA", "span_id": "a1", "seq": 0, "name": "router.route"},
            {"trace_id": "tB", "span_id": "b1", "seq": 2, "name": "router.route"},
        )
        run_data = {"conversations": [{"turn_details": [
            {"turn_num": 1, "trace_id": "tA"},
            {"turn_num": 2, "trace_id": "tB"},
        ]}]}

        attached, by_trace = attach_traces(run_data, self.decisions)

        self.assertEqual(attached, 2)
        turns = run_data["conversations"][0]["turn_details"]
        # turn 1 gets tA's two spans, ORDERED by seq (root before child)
        self.assertEqual([s["name"] for s in turns[0]["trace"]],
                         ["router.route", "router.llm_tools"])
        # turn 2 gets tB's one span
        self.assertEqual([s["name"] for s in turns[1]["trace"]], ["router.route"])

    def test_turn_without_matching_trace_gets_empty_list(self) -> None:
        self._write({"trace_id": "tX", "span_id": "x", "seq": 0, "name": "router.route"})
        run_data = {"conversations": [{"turn_details": [
            {"turn_num": 1, "trace_id": "nope"},
        ]}]}
        attached, _ = attach_traces(run_data, self.decisions)
        self.assertEqual(attached, 0)
        self.assertEqual(run_data["conversations"][0]["turn_details"][0]["trace"], [])

    def test_missing_decisions_file_is_safe(self) -> None:
        run_data = {"conversations": [{"turn_details": [{"trace_id": "t"}]}]}
        attached, by_trace = attach_traces(run_data, self.dir / "nope.jsonl")
        self.assertEqual(attached, 0)
        self.assertEqual(by_trace, {})
        self.assertEqual(run_data["conversations"][0]["turn_details"][0]["trace"], [])


if __name__ == "__main__":
    unittest.main()

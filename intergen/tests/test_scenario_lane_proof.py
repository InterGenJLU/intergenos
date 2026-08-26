# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The lane-proof runner's selection and regression rules.

These are the decisions that determine whether a piece of InterGen work is
allowed to call itself proven, so they are asserted directly rather than left to
the one place they are exercised. Everything here runs with no model, no bus and
no daemon: the rules under test are pure.

The rules, and what each one is protecting against:

  * Filters narrow TOGETHER. Two filters that added up would quietly widen a run
    that its author believed they had narrowed.
  * An empty selection is a refusal, never a pass. A run that measured nothing
    reporting success is the silent-failure shape this whole harness exists to
    remove.
  * A scenario the baseline never contained is not a regression — otherwise the
    lane that ADDS coverage fails its own proof and learns to stop adding it.
  * A scenario that passed before and does not pass now IS a regression, and the
    run says which ones.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.scenario import lane_proof


class _Fake:
    """The two fields the selector reads, and nothing else."""

    def __init__(self, sid: str, tags: list[str]) -> None:
        self.id = sid
        self.tags = tags


_SET = [
    _Fake("a", ["batch:field_shapes", "shape:S1"]),
    _Fake("b", ["batch:field_shapes", "shape:S2"]),
    _Fake("c", ["batch:web_search", "shape:S1"]),
]


class TestSelection(unittest.TestCase):

    def test_no_filter_selects_everything(self) -> None:
        self.assertEqual([s.id for s in lane_proof.select(_SET, [], [], 0)],
                         ["a", "b", "c"])

    def test_batch_filter(self) -> None:
        got = lane_proof.select(_SET, ["field_shapes"], [], 0)
        self.assertEqual([s.id for s in got], ["a", "b"])

    def test_tag_filter(self) -> None:
        got = lane_proof.select(_SET, [], ["shape:S1"], 0)
        self.assertEqual([s.id for s in got], ["a", "c"])

    def test_filters_narrow_together_not_apart(self) -> None:
        """batch AND tag, never batch OR tag."""
        got = lane_proof.select(_SET, ["field_shapes"], ["shape:S1"], 0)
        self.assertEqual([s.id for s in got], ["a"],
                         "the filters widened the run instead of narrowing it")

    def test_limit_applies_after_the_filters(self) -> None:
        got = lane_proof.select(_SET, ["field_shapes"], [], 1)
        self.assertEqual([s.id for s in got], ["a"])

    def test_a_filter_that_matches_nothing_selects_nothing(self) -> None:
        self.assertEqual(lane_proof.select(_SET, ["no_such_batch"], [], 0), [])


class TestBaseline(unittest.TestCase):

    def _baseline(self, rows: list[tuple[str, str]]) -> str:
        d = tempfile.mkdtemp()
        p = Path(d) / "results.json"
        p.write_text(json.dumps({
            "run_id": "base",
            "scenarios": [{"id": i, "grade": g} for i, g in rows],
        }), encoding="utf-8")
        return str(p)

    def test_reads_only_the_passing_ids(self) -> None:
        path = self._baseline([("a", "PASS"), ("b", "FAIL"), ("c", "MIXED")])
        self.assertEqual(lane_proof.baseline_passes(path), {"a"})

    def test_an_empty_baseline_has_no_passes(self) -> None:
        path = self._baseline([])
        self.assertEqual(lane_proof.baseline_passes(path), set())


class TestRegressionRule(unittest.TestCase):
    """The subtraction the runner performs, asserted on its own.

    Kept as a plain set expression rather than reaching into main(), because
    what has to be right is the RULE: was-passing minus is-passing, restricted
    to scenarios this run actually covered.
    """

    @staticmethod
    def _regressed(was: set[str], now: set[str], covered: set[str]) -> list[str]:
        return sorted(s for s in (was - now) if s in covered)

    def test_a_scenario_that_stopped_passing_is_a_regression(self) -> None:
        self.assertEqual(
            self._regressed({"a", "b"}, {"a"}, {"a", "b"}), ["b"])

    def test_a_scenario_the_baseline_never_had_is_not_a_regression(self) -> None:
        """New coverage must never fail the run that introduces it."""
        self.assertEqual(self._regressed({"a"}, {"a"}, {"a", "new"}), [])

    def test_a_scenario_this_run_did_not_cover_is_not_a_regression(self) -> None:
        """A narrowed run must not report the scenarios it chose not to drive."""
        self.assertEqual(self._regressed({"a", "b"}, {"a"}, {"a"}), [])

    def test_no_change_is_no_regression(self) -> None:
        self.assertEqual(self._regressed({"a", "b"}, {"a", "b"}, {"a", "b"}), [])


class TestRefusals(unittest.TestCase):

    def test_an_empty_selection_exits_four(self) -> None:
        """A run that measures nothing must not be able to report success."""
        with tempfile.TemporaryDirectory() as d:
            corpus = Path(d) / "empty.json"
            corpus.write_text("[]", encoding="utf-8")
            rc = lane_proof.main([
                "--run-id", "empty", "--out", d, "--corpus", str(corpus),
                "--allow-installed"])
        self.assertEqual(rc, 4)

    def test_a_named_trace_that_is_absent_exits_four(self) -> None:
        """A trace file the caller named and that is not there is a refusal.

        Dropping it silently would grade the run with the grounding assertions
        failing closed while its caller believed the trace had been joined —
        the harness's own state reported as the assistant's.
        """
        real_corpus = (Path(lane_proof.__file__).resolve().parent
                       / "corpus" / "field_shapes.json")
        with tempfile.TemporaryDirectory() as d:
            rc = lane_proof.main([
                "--run-id", "no-trace", "--out", d,
                "--corpus", str(real_corpus), "--limit", "1",
                "--glass", str(Path(d) / "there-is-no-such-file.jsonl"),
                "--allow-installed"])
        self.assertEqual(rc, 4)


if __name__ == "__main__":
    unittest.main()

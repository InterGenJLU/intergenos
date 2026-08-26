# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The harness must read the decomposition the router actually emitted.

THE GAP, read in the tree. The router emits its decomposer verdict as an
always-on glass row — ``decision`` / ``compound_route``, carrying
``sub_queries`` — on every turn. The scenario harness reads that verdict only
when a run hands it a glass source: ``build_trace_lookup`` in
``intergen/tests/scenario/live_run.py`` joins ``sub_queries`` from
``glass_rows``, and does nothing at all when the caller passes none. Nothing
tells the run that the source is missing.

WHAT THAT PRODUCED. A whole-corpus run that supplied no glass source graded
every ``decomposes_into`` assertion against an empty ``sub_queries`` list, so
``_eval_decomposes_into`` in ``intergen/tests/scenario/grader.py`` reported
"no decomposition observed (trace carries no sub_queries)" for ten scenarios —
including four whose request the decomposer does split, and one whose recorded
reply is the decomposer's own two-action message. The report named a product
defect where the real state was an instrument with no input.

WHAT THIS FILE PINS. Three separate things:

  1. The harvest: with the always-on glass file in place and no glass rows
     passed in, the lookup still resolves the turn's ``sub_queries``.
  2. The report: a turn graded with NO source that could carry a decomposition
     says exactly that, instead of reporting that the router did not decompose.
     It still FAILS — an ungraded assertion is never a pass — but it fails
     saying which of the two states it is in.
  3. The control: with a source joined and the router genuinely not splitting
     the request, the old wording is still what the run gets.

The reality proof is case 1's row: it is built by running the real decomposer
over a real corpus sentence and emitting a real glass row through the real
writer, then reading it back through the harness.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from intergen.interfaces.types import HardwareTierLevel
from intergen.decomposer import analyze_query
from intergen.tests.scenario import live_run
from intergen.tests.scenario.grader import grade_turn
from intergen.tests.scenario.schema import Assertion, Turn
from intergen.tests.scenario.transport import TurnResult

# A corpus sentence the decomposer does split (writing_help.json,
# WRT-do-for-me-01), used so this file measures the real verdict rather than a
# hand-written one.
_COMPOUND = "find a pdf editor and install it"


@contextlib.contextmanager
def _glass_under(state_home: Path):
    """Point the real glass writer at a throwaway state directory.

    The writer resolves its directory once, at construction, so the singleton is
    dropped and rebuilt inside this block and restored on the way out. The module
    is NOT reloaded: other tests in the same process hold references to its
    functions and compare them by identity, and a reload would hand them a
    different object.
    """
    import intergen.glass as glass
    previous_home = os.environ.get("XDG_STATE_HOME")
    previous_logger = glass._glass
    os.environ["XDG_STATE_HOME"] = str(state_home)
    glass._glass = None
    try:
        yield glass
    finally:
        glass._glass = previous_logger
        if previous_home is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = previous_home


def _emit_real_glass_row(glass, turn_id: str, query: str) -> None:
    """Run the real decomposer and write its verdict through the real writer.

    The row's shape is not transcribed here — it is produced by the same
    ``glass.emit`` call the router makes, into whatever glass file the writer is
    currently pointed at, so a change to either end of the contract shows up as
    a failure in this file.
    """
    decomposition = analyze_query(query, HardwareTierLevel.TIER_1)
    glass.emit("decision", "compound_route", turn_id=turn_id, detail={
        "is_compound": decomposition.is_compound,
        "needs_decomposition": decomposition.needs_decomposition,
        "sub_queries": decomposition.sub_queries,
        "route_compound_whole": False,
        "decomposed": decomposition.needs_decomposition,
    })


def _glass_rows(state_home: Path) -> list[dict]:
    path = state_home / "intergen" / "glass.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


class TheAlwaysOnGlassFileIsReadWithoutBeingNamed(unittest.TestCase):
    """Case 1 — the harvest. The source is always on; the harness reads it."""

    def test_sub_queries_resolve_with_no_glass_rows_passed_in(self) -> None:
        with tempfile.TemporaryDirectory(prefix="harvest-") as tmp:
            state_home = Path(tmp) / "state"
            state_home.mkdir(parents=True)
            turn_id = "harvest0000000001"
            # The whole case runs with the writer AND the reader pointed at the
            # throwaway state directory: the harness resolves the canonical path
            # when it looks, so the redirection has to still be in force then.
            with _glass_under(state_home) as glass:
                _emit_real_glass_row(glass, turn_id, _COMPOUND)

                written = _glass_rows(state_home)
                self.assertNotEqual(
                    written, [],
                    "control: the real writer wrote nothing, so this case "
                    "would measure a harvest with nothing to harvest")
                emitted = [r for r in written
                           if (r.get("detail") or {}).get("sub_queries")]
                self.assertNotEqual(
                    emitted, [],
                    "control: the real decomposer did not split "
                    f"{_COMPOUND!r}, so this case has no decomposition to find")

                lookup = live_run.build_trace_lookup()
                view = lookup(TurnResult(text="ok", trace_id=turn_id))
            self.assertIsNotNone(view)
            self.assertNotEqual(
                view.sub_queries, [],
                "the harness did not read the decomposition the router wrote "
                "to the always-on glass file")


class AnUngradedDecompositionSaysSo(unittest.TestCase):
    """Case 2 — the report. No source joined is not the same as no split."""

    @staticmethod
    def _turn() -> Turn:
        return Turn(user=_COMPOUND,
                    assertions=[Assertion(type="decomposes_into", value="2")])

    def test_no_source_joined_is_named_as_such(self) -> None:
        grade = grade_turn(self._turn(), TurnResult(text="ok"), trace=None)
        failed = [r for r in grade.results if r.type == "decomposes_into"]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0].passed,
                         "an assertion nothing was read for must never pass")
        self.assertIn(
            "no decomposition trace", failed[0].actual,
            "the run was told the router did not decompose when the real "
            f"state is that nothing was read: {failed[0].actual!r}")


class AJoinedSourceThatShowsNoSplitStillReportsThat(unittest.TestCase):
    """Case 3 — the control. A real 'it did not decompose' keeps its wording."""

    def test_joined_but_empty_is_the_old_message(self) -> None:
        from intergen.tests.scenario.trace import TraceView
        trace = TraceView.from_glass_rows(
            [{"turn_id": "t1", "phase": "decision", "event": "compound_route",
              "detail": {"sub_queries": [], "needs_decomposition": False}}],
            trace_id="t1")
        turn = Turn(user="what time is it",
                    assertions=[Assertion(type="decomposes_into", value="2")])
        grade = grade_turn(turn, TurnResult(text="ok"), trace=trace)
        res = [r for r in grade.results if r.type == "decomposes_into"][0]
        self.assertFalse(res.passed)
        self.assertIn("no decomposition observed", res.actual)


if __name__ == "__main__":
    unittest.main()

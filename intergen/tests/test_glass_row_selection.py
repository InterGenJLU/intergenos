# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A test must read the turn record by WHAT a row is, not by where it sits.

THE DEFECT CLASS. The turn record's writer emits rows of its own around a turn.
It already emits a sequence-resumed row when it opens the file, a rotation marker
when the file rolls, and a synthesized terminal when a turn ends without one —
each of those arrived in a release, and each moved the position of every row a
test had emitted. Measured on this tree: after the writer gained its opening row,
a corpus file that indexed row zero reported 29 failures where 19 were real, and
the ten extra were its own CONTROLS failing with a KeyError. A failing control
reads as a broken measurement, so the cost is not only the wasted run: it hides
which half of the file was telling the truth.

The rule: name the row. This file asserts the shared helper that makes naming it
cheap, and it asserts the defect directly — that position zero of a real record
is NOT the row a caller emitted, so any test that reads position zero is reading
the writer's bookkeeping.

WHAT THIS FILE DOES NOT DO. It does not scan the test corpus for offenders. The
sweep that found them is in this change's sealed evidence, and the conversions
are in the same commit as the helper; a scanner over sibling test files would
fail for reasons that have nothing to do with the record and would be one more
instrument to keep honest.
"""
from __future__ import annotations

import os
import tempfile
import unittest

import intergen.glass as glass


def _helper():
    """The shared row-selection helper, imported at call time.

    Imported here rather than at module level so a module that does not exist
    yet fails each test with a sentence instead of breaking collection for the
    whole file and hiding every other case behind an import error.
    """
    try:
        from intergen.tests import glass_rows
    except ImportError as exc:
        raise AssertionError(
            "intergen.tests.glass_rows does not exist. Tests still read the "
            "turn record by list position, so the next row the writer learns "
            "to emit moves what they measure — and it moves their controls "
            "too, which makes a broken measurement look like a worse defect."
        ) from exc
    return glass_rows


def _fresh_record() -> str:
    tmp = tempfile.mkdtemp(prefix="glass-row-selection-")
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None
    return tmp


class PositionZeroIsNotTheRowATestEmitted(unittest.TestCase):
    """The defect, against the shipped writer — no simulation involved."""

    def test_the_first_row_of_a_real_record_is_the_writers_own(self) -> None:
        tmp = _fresh_record()
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled", detail={"text": "hello"})
        rows = _helper().read(tmp)
        self.assertGreater(len(rows), 1,
                           "control: the writer emitted nothing of its own, so "
                           "this test measured nothing")
        self.assertNotEqual(
            (rows[0].get("phase"), rows[0].get("event")),
            ("prompt", "assembled"),
            "position zero happens to be the emitted row here, which would "
            "make the whole class invisible; the writer's own opening row is "
            "what should be first")

    def test_naming_the_row_finds_it_wherever_it_sits(self) -> None:
        tmp = _fresh_record()
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled", detail={"text": "hello"})
        glass_rows = _helper()
        row = glass_rows.only(glass_rows.read(tmp),
                              phase="prompt", event="assembled")
        self.assertEqual(row["detail"]["text"], "hello")


class TheHelperSaysWhatItFound(unittest.TestCase):
    """Selection, counting, and what happens when nothing matches."""

    def setUp(self) -> None:
        self.tmp = _fresh_record()
        with glass.turn(glass.new_turn_id(), "web"):
            glass.emit("route", "turn_start", detail={"n": 1})
            glass.emit("route", "verdict", detail={"n": 2})
            glass.emit("route", "verdict", detail={"n": 3})
            glass.emit("delivery", "final", detail={"n": 4})

    @property
    def rows(self):
        """Read in each test, not in setUp: a missing helper must fail the case
        it belongs to with a sentence, not error every case in this class."""
        return _helper().read(self.tmp)

    def test_a_missing_record_reads_as_no_rows(self) -> None:
        self.assertEqual(_helper().read(tempfile.mkdtemp()), [])

    def test_where_counts_the_selected_rows_not_the_file(self) -> None:
        glass_rows = _helper()
        self.assertEqual(len(glass_rows.where(self.rows,
                                              phase="route", event="verdict")), 2)
        self.assertEqual(len(glass_rows.where(self.rows, phase="delivery")), 1)

    def test_first_and_last_pick_the_ends_of_the_selection(self) -> None:
        glass_rows = _helper()
        self.assertEqual(
            glass_rows.first(self.rows, phase="route", event="verdict")["detail"]["n"], 2)
        self.assertEqual(
            glass_rows.last(self.rows, phase="route", event="verdict")["detail"]["n"], 3)

    def test_only_refuses_an_ambiguous_selection(self) -> None:
        glass_rows = _helper()
        with self.assertRaises(AssertionError) as caught:
            glass_rows.only(self.rows, phase="route", event="verdict")
        self.assertIn("found 2", str(caught.exception))

    def test_nothing_matching_names_what_the_record_holds(self) -> None:
        glass_rows = _helper()
        with self.assertRaises(AssertionError) as caught:
            glass_rows.only(self.rows, phase="prompt", event="assembled")
        message = str(caught.exception)
        self.assertIn("phase='prompt'", message)
        self.assertIn("route/verdict", message,
                      "the error must say what IS there, or the next reader "
                      "learns nothing from it")

    def test_a_turn_id_criterion_selects_that_turn(self) -> None:
        rows = _helper().where(self.rows, turn_id="no-such-turn")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()

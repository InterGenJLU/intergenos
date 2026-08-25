# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The trace gate's release bound, proved against inputs it must and must not drop.

WHY THIS FILE EXISTS. The installed-system trace gate asserts two properties of
the rows a running daemon wrote: that each can be joined to its turn, and that
the record can be put back in order. The trace file is append-only and its writer
rotates by SIZE alone, so on an upgraded machine the rows a PREVIOUS release
wrote are still in it — measured on a real install, 37 KB against a 64 MB
rotation threshold, which is effectively forever. Unbounded, the gate asserts
those properties against rows that no shipped change can alter, so a release that
fixed them would still fail and, because the release gate refuses on any failing
gate, no upgraded machine could validate any release.

The bound is what makes the gate a statement about the release under test. It is
therefore exactly as dangerous as it is useful: a bound that dropped too much
would quietly empty the gate. So it is proved here, in the ordinary suite, on
three inputs — a row that must be dropped, a row that must NOT be dropped, and a
file where the bound leaves nothing, which must FAIL rather than pass.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

import pytest

INSTALLED_AT = 1_000_000.0
BEFORE = INSTALLED_AT - 60.0
AFTER = INSTALLED_AT + 60.0


def _gate():
    """Load the installed-tier trace gate by path.

    By path because tests/installed/ is an env-gated tier with its own conftest,
    not an importable package, and because the file the tier actually runs is the
    file this test has to measure.
    """
    root = Path(__file__).resolve().parents[2]
    path = root / "tests" / "installed" / "test_gate_glass_trace_integrity.py"
    if not path.is_file():
        raise AssertionError(f"{path} does not exist")
    spec = importlib.util.spec_from_file_location("_igos_trace_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RowsSinceTests(unittest.TestCase):
    """What the bound keeps and what it drops."""

    def test_a_row_written_before_the_install_is_dropped(self):
        rows = [{"ts": BEFORE, "turn_id": "old"}, {"ts": AFTER, "turn_id": "new"}]
        in_scope, older, undatable = _gate().rows_since(rows, INSTALLED_AT)
        self.assertEqual([r["turn_id"] for r in in_scope], ["new"])
        self.assertEqual(older, 1)
        self.assertEqual(undatable, 0)

    def test_a_row_written_exactly_at_the_install_is_kept(self):
        rows = [{"ts": INSTALLED_AT, "turn_id": "boundary"}]
        in_scope, older, _ = _gate().rows_since(rows, INSTALLED_AT)
        self.assertEqual(len(in_scope), 1)
        self.assertEqual(older, 0)

    def test_a_row_with_no_readable_timestamp_is_kept_and_counted(self):
        """An unplaceable row is not proof that it predates the install.

        Dropping it would let the gate go quiet about rows it never examined,
        which is the failure mode the bound itself could introduce.
        """
        rows = [{"turn_id": "no-ts"}, {"ts": "not-a-number", "turn_id": "bad-ts"},
                {"ts": True, "turn_id": "bool-is-not-a-time"}]
        in_scope, older, undatable = _gate().rows_since(rows, INSTALLED_AT)
        self.assertEqual(len(in_scope), 3)
        self.assertEqual(older, 0)
        self.assertEqual(undatable, 3)

    def test_the_bound_does_not_drop_rows_on_a_freshly_installed_machine(self):
        """Every row after the install stays in scope — the ordinary case.

        Stated as its own case because a bound that quietly shrank the row set on
        a normal machine would weaken the gate everywhere while looking correct
        on the two cases above.
        """
        rows = [{"ts": AFTER + i, "turn_id": f"t{i}"} for i in range(20)]
        in_scope, older, undatable = _gate().rows_since(rows, INSTALLED_AT)
        self.assertEqual(len(in_scope), 20)
        self.assertEqual((older, undatable), (0, 0))


class EmptyAfterBoundingTests(unittest.TestCase):
    """A bound that leaves nothing must FAIL, never pass."""

    def _run_fixture(self, rows, tmp: Path, install_date: float):
        gate = _gate()
        state = tmp / ".local" / "state" / "intergen"
        state.mkdir(parents=True, exist_ok=True)
        (state / "glass.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        gate.installed_release_install_date = lambda: install_date
        # The fixture's body, called directly: this is the code the tier runs.
        return gate.trace_rows.__wrapped__(tmp)

    def test_a_file_holding_only_pre_install_rows_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rows = [{"ts": BEFORE, "turn_id": "no-turn", "seq": 0}]
            with self.assertRaises(BaseException) as caught:
                self._run_fixture(rows, Path(d), INSTALLED_AT)
            message = str(caught.exception)
            self.assertIn("NONE of them was written on or after", message)
            self.assertIn("rows older than the install: 1", message)

    def test_a_file_with_one_row_after_the_install_is_measured(self):
        """The control for the case above: one in-scope row and the gate proceeds.

        Without this, a bound that failed on EVERY input would pass the test above
        and look like a working refusal.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rows = [{"ts": BEFORE, "turn_id": "old", "seq": 0},
                    {"ts": AFTER, "turn_id": "new", "seq": 1}]
            in_scope, unparseable, path, bound = self._run_fixture(
                rows, Path(d), INSTALLED_AT)
            self.assertEqual([r["turn_id"] for r in in_scope], ["new"])
            self.assertEqual(unparseable, 0)
            self.assertEqual(bound["total"], 2)
            self.assertEqual(bound["older"], 1)
            self.assertTrue(str(path).endswith("glass.jsonl"))

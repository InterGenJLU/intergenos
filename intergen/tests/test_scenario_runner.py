# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-2.2 — the scenario runner: boundaries, write-gap, linked-pair cleanup.

Drives whole scenarios with no daemon and no model: the mock transport records
the session boundaries the runner applies, and a small DB-backed transport
writes/omits fact rows in a fixture memory DB so the memory-write-gap check and
the producer→consumer linked-pair cleanup are exercised deterministically. The
live ClientTransport restart primitive is exercised against a real daemon by the
seed-scenario runs; here the CONTRACT (order, gap, sweep) is pinned.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from intergen.tests.scenario.schema import Assertion, Scenario, Turn
from intergen.tests.scenario.transport import (
    MockTransport,
    ScenarioTransport,
    TurnResult,
)
from intergen.tests.scenario.runner import run_scenario, run_scenarios

_FACTS_DDL = """
CREATE TABLE facts (fact_id TEXT PRIMARY KEY, key TEXT, value TEXT, category TEXT,
    source TEXT, confidence REAL, created_at REAL NOT NULL, updated_at REAL,
    deleted INTEGER DEFAULT 0)
"""
_SESSIONS_DDL = """
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, topic TEXT, queries TEXT,
    tools_used TEXT, started_at REAL NOT NULL, ended_at REAL, turn_count INTEGER DEFAULT 0)
"""
_FIXED_CLOCK = 1000.0        # pre-run snapshot cutoff
_RUN_ERA = 1500.0            # a fact written during the run (>= cutoff)


def _mkdb(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_FACTS_DDL + ";" + _SESSIONS_DDL)
    conn.commit()
    conn.close()


def _add_fact(path: str, fid: str, created_at: float) -> None:
    conn = sqlite3.connect(path)
    conn.execute("INSERT OR REPLACE INTO facts (fact_id, key, value, created_at) "
                 "VALUES (?,?,?,?)", (fid, f"k-{fid}", f"v-{fid}", created_at))
    conn.commit()
    conn.close()


def _del_fact(path: str, fid: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM facts WHERE fact_id = ?", (fid,))
    conn.commit()
    conn.close()


def _fact_ids(path: str) -> set[str]:
    conn = sqlite3.connect(path)
    ids = {r[0] for r in conn.execute("SELECT fact_id FROM facts")}
    conn.close()
    return ids


class _DBTransport(ScenarioTransport):
    """A transport whose ask() writes/deletes fixture fact rows, so a scenario's
    store/forget actually change the DB — enough to exercise the runner's
    write-gap and linked-pair cleanup without a daemon."""

    def __init__(self, db_path: str, *, writes=None, deletes=None, replies=None):
        self._db = db_path
        self._writes = writes or {}      # message -> (fact_id, created_at)
        self._deletes = deletes or {}    # message -> fact_id
        self._replies = replies or {}
        self.boundaries: list[str] = []
        self.reset_count = 0

    def ask(self, message: str) -> TurnResult:
        if message in self._writes:
            _add_fact(self._db, *self._writes[message])
        if message in self._deletes:
            _del_fact(self._db, self._deletes[message])
        return self._replies.get(message, TurnResult(text="ok", source="memory"))

    def reset(self) -> None:
        self.reset_count += 1

    def await_ready(self, timeout_s=None) -> None:
        pass

    def memory_db_path(self) -> str | None:
        return self._db

    def restart_daemon(self) -> None:
        self.boundaries.append("restart-before")

    def new_session(self) -> None:
        self.boundaries.append("new-session-before")


def _scn(sid, turns, **kw):
    return Scenario(id=sid, name=sid, axis=kw.pop("axis", ["routing"]),
                    turns=turns, category=kw.pop("category", ""), **kw)


class BoundaryApplicationTests(unittest.TestCase):
    def test_markers_applied_in_order_before_each_turn(self):
        t = MockTransport(replies={
            "a": TurnResult(text="ok"), "b": TurnResult(text="ok"),
            "c": TurnResult(text="ok")})
        scn = _scn("S", [
            Turn(user="a"),
            Turn(user="b", session_marker="restart-before"),
            Turn(user="c", session_marker="new-session-before"),
        ])
        run = run_scenario(scn, t)
        self.assertEqual(run.boundaries, ["restart-before", "new-session-before"])
        self.assertEqual(t.boundaries, ["restart-before", "new-session-before"])
        self.assertEqual(t.restart_count, 1)
        self.assertEqual(t.new_session_count, 1)
        # scenario-start reset happened once; restart-before also re-armed ready.
        self.assertEqual(t.reset_count, 1)
        self.assertGreaterEqual(t.ready_calls, 1)

    def test_no_markers_no_boundaries(self):
        t = MockTransport()
        run = run_scenario(_scn("S", [Turn(user="hi")]), t)
        self.assertEqual(run.boundaries, [])
        self.assertEqual(t.restart_count, 0)


class GradeTests(unittest.TestCase):
    def test_simple_scenario_grades_pass(self):
        t = MockTransport(replies={"ping": TurnResult(text="pong here")})
        scn = _scn("S", [Turn(user="ping",
                              assertions=[Assertion("contains", "pong")])])
        run = run_scenario(scn, t)
        self.assertTrue(run.passed)
        self.assertEqual(run.grade.grade, "PASS")
        # No DB → write-gap not checked, no cleanup claim.
        self.assertFalse(run.write_gap.is_gap)
        self.assertFalse(run.write_gap.checked)
        self.assertIsNone(run.cleanup)


class MemoryWriteGapTests(unittest.TestCase):
    def test_producer_that_stores_nothing_is_a_hard_gap(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"
            _mkdb(db)
            # producer (cleanup=False, memory axis) whose ask writes NO fact
            t = _DBTransport(str(db))  # no writes mapping
            scn = _scn("MEM-store-01",
                       [Turn(user="remember that my editor is neovim",
                             assertions=[Assertion("contains", "ok")])],
                       axis=["memory_persistence"], category="memory",
                       cleanup=False)
            run = run_scenario(scn, t, clock=lambda: _FIXED_CLOCK)
            self.assertTrue(run.write_gap.checked)
            self.assertTrue(run.write_gap.is_gap)
            self.assertEqual(run.grade.grade, "FAIL")   # downgraded by the gap
            self.assertFalse(run.passed)
            self.assertIn("MEMORY WRITE GAP", run.write_gap.render())

    def test_producer_that_stores_a_fact_has_no_gap(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"
            _mkdb(db)
            msg = "remember that my editor is neovim"
            t = _DBTransport(str(db), writes={msg: ("editor", _RUN_ERA)})
            scn = _scn("MEM-store-01",
                       [Turn(user=msg, assertions=[Assertion("contains", "ok")])],
                       axis=["memory_persistence"], category="memory",
                       cleanup=False)
            run = run_scenario(scn, t, clock=lambda: _FIXED_CLOCK)
            self.assertTrue(run.write_gap.checked)
            self.assertFalse(run.write_gap.is_gap)
            # producer leaves the fact alive (cleanup=False → no cleanup claim)
            self.assertIsNone(run.cleanup)
            self.assertEqual(_fact_ids(str(db)), {"editor"})


class LinkedPairTests(unittest.TestCase):
    def test_consumer_sweeps_producer_residue_the_forget_missed(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"
            _mkdb(db)
            store = "remember that my editor is neovim"
            recall = "what's my editor?"
            forget = "forget that my editor is neovim"
            # The forget does NOT actually delete (a forget flow that missed);
            # cleanup_for must still sweep the producer's fact.
            t = _DBTransport(str(db), writes={store: ("editor", _RUN_ERA)})
            producer = _scn("MEM-store-01",
                            [Turn(user=store, assertions=[Assertion("contains", "ok")])],
                            axis=["memory_persistence"], category="memory",
                            cleanup=False)
            consumer = _scn("MEM-recall-forget-01", [
                Turn(user=recall, session_marker="restart-before",
                     assertions=[Assertion("contains", "ok")]),
                Turn(user=forget, assertions=[Assertion("contains", "ok")]),
                Turn(user=recall, session_marker="restart-before",
                     assertions=[Assertion("contains", "ok")]),
            ], axis=["memory_persistence"], category="memory",
               cleanup=True, cleanup_for=["MEM-store-01"])
            # Pass consumer FIRST — the runner must still run the producer first.
            runs = run_scenarios([consumer, producer], t, clock=lambda: _FIXED_CLOCK)
            self.assertEqual([r.scenario_id for r in runs],
                             ["MEM-store-01", "MEM-recall-forget-01"])
            consumer_run = runs[1]
            # cleanup_for swept the producer's fact even though forget missed it.
            self.assertEqual(consumer_run.cleanup.deleted_facts, 1)
            self.assertFalse(consumer_run.cleanup.incomplete)
            self.assertFalse(consumer_run.leaks.leaked)
            self.assertEqual(_fact_ids(str(db)), set())  # DB clean after the pair

    def test_ordering_places_every_producer_before_its_consumer(self):
        a = _scn("A", [Turn(user="x")], cleanup=False)
        b = _scn("B", [Turn(user="y")], cleanup=True, cleanup_for=["A"])
        t = MockTransport()
        runs = run_scenarios([b, a], t)
        self.assertEqual([r.scenario_id for r in runs], ["A", "B"])


if __name__ == "__main__":
    unittest.main()

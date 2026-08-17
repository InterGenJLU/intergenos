# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-2.1 — run isolation: snapshot / delta-cleanup / leak detection.

Proves the harness is safe against a live daemon's real memory: cleanup removes
only what the run created, never a pre-existing row (even one whose timestamp
reads at/after the cutoff), and a run-era row that survives is reported.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from intergen.tests.scenario.isolation import (
    CleanupResult,
    LeakReport,
    delta_cleanup,
    detect_leaks,
    snapshot,
)

_FACTS_DDL = """
CREATE TABLE facts (fact_id TEXT PRIMARY KEY, key TEXT, value TEXT, category TEXT,
    source TEXT, confidence REAL, created_at REAL NOT NULL, updated_at REAL,
    deleted INTEGER DEFAULT 0)
"""
_SESSIONS_DDL = """
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, topic TEXT, queries TEXT,
    tools_used TEXT, started_at REAL NOT NULL, ended_at REAL, turn_count INTEGER DEFAULT 0)
"""


def _mkdb(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_FACTS_DDL + ";" + _SESSIONS_DDL)
    conn.commit()
    conn.close()


def _add_fact(path: Path, fid: str, created_at: float) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("INSERT INTO facts (fact_id, key, value, created_at) VALUES (?,?,?,?)",
                 (fid, f"k-{fid}", f"v-{fid}", created_at))
    conn.commit()
    conn.close()


def _add_session(path: Path, sid: str, started_at: float) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("INSERT INTO sessions (session_id, topic, started_at) VALUES (?,?,?)",
                 (sid, "t", started_at))
    conn.commit()
    conn.close()


def _fact_ids(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    ids = {r[0] for r in conn.execute("SELECT fact_id FROM facts")}
    conn.close()
    return ids


class SnapshotTests(unittest.TestCase):
    def test_absent_db_is_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            snap = snapshot(Path(d) / "nope.db", cutoff=100.0)
            self.assertEqual(snap.fact_ids, frozenset())
            self.assertEqual(snap.session_ids, frozenset())

    def test_captures_existing_ids(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"
            _mkdb(db)
            _add_fact(db, "f1", 50.0)
            _add_session(db, "s1", 50.0)
            snap = snapshot(db, cutoff=100.0)
            self.assertEqual(snap.fact_ids, frozenset({"f1"}))
            self.assertEqual(snap.session_ids, frozenset({"s1"}))


class DeltaCleanupTests(unittest.TestCase):
    def test_deletes_run_rows_preserves_preexisting(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"
            _mkdb(db)
            _add_fact(db, "pre", 50.0)          # pre-existing user memory
            _add_session(db, "pre-s", 50.0)
            snap = snapshot(db, cutoff=100.0)
            _add_fact(db, "run", 150.0)         # created during the run
            _add_session(db, "run-s", 150.0)
            res = delta_cleanup(snap)
            self.assertEqual(res.deleted_facts, 1)
            self.assertEqual(res.deleted_sessions, 1)
            self.assertFalse(res.incomplete)
            self.assertEqual(_fact_ids(db), {"pre"})  # user's fact untouched

    def test_clock_skew_preexisting_row_never_deleted(self):
        # A pre-existing row whose timestamp reads AT/AFTER the cutoff (clock skew
        # or a re-touch) must still be preserved because its id is in the baseline
        # — the safety belt against deleting real user memory.
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"
            _mkdb(db)
            _add_fact(db, "pre-late", 50.0)
            snap = snapshot(db, cutoff=100.0)   # baseline includes pre-late
            # Simulate the row's timestamp moving to run-era (still same id).
            conn = sqlite3.connect(str(db))
            conn.execute("UPDATE facts SET created_at = 200.0 WHERE fact_id = 'pre-late'")
            conn.commit(); conn.close()
            res = delta_cleanup(snap)
            self.assertEqual(res.deleted_facts, 0)
            self.assertEqual(_fact_ids(db), {"pre-late"})

    def test_no_rows_is_clean_noop(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"
            _mkdb(db)
            snap = snapshot(db, cutoff=100.0)
            res = delta_cleanup(snap)
            self.assertFalse(res.incomplete)
            self.assertEqual(res.deleted_facts, 0)


class ArtifactCleanupTests(unittest.TestCase):
    def test_run_era_file_removed_preexisting_kept(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"; _mkdb(db)
            adir = Path(d) / "artifacts"; adir.mkdir()
            pre = adir / "pre.txt"; pre.write_text("keep")
            import os
            os.utime(pre, (50.0, 50.0))          # pre-existing (old mtime)
            snap = snapshot(db, artifact_dirs=[adir], cutoff=100.0)
            run = adir / "run.txt"; run.write_text("litter")  # created during run
            res = delta_cleanup(snap, artifact_dirs=[adir])
            self.assertEqual(res.deleted_files, 1)
            self.assertFalse(res.incomplete)
            self.assertTrue(pre.exists())
            self.assertFalse(run.exists())


class LeakDetectionTests(unittest.TestCase):
    def test_leak_is_post_minus_pre(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"; _mkdb(db)
            _add_fact(db, "pre", 50.0)
            pre = snapshot(db, cutoff=100.0)
            _add_fact(db, "leaked", 150.0)       # survived (a cleanup that missed it)
            post = snapshot(db, cutoff=200.0)
            report = detect_leaks(pre, post)
            self.assertTrue(report.leaked)
            self.assertEqual(report.new_facts, ["leaked"])

    def test_no_leak_when_clean(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"; _mkdb(db)
            _add_fact(db, "pre", 50.0)
            pre = snapshot(db, cutoff=100.0)
            post = snapshot(db, cutoff=200.0)
            self.assertFalse(detect_leaks(pre, post).leaked)


class ReportingTests(unittest.TestCase):
    def test_incomplete_reporting(self):
        r = CleanupResult(deleted_facts=1, residual_facts=["survivor"])
        self.assertTrue(r.incomplete)
        self.assertIn("CLEANUP INCOMPLETE", r.render())

    def test_clean_reporting(self):
        self.assertIn("clean", CleanupResult(deleted_facts=2).render())

    def test_leak_render(self):
        self.assertIn("LEAK", LeakReport(new_facts=["x"]).render())
        self.assertIn("no leak", LeakReport().render())


if __name__ == "__main__":
    unittest.main()

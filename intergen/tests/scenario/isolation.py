# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-2.1 — run isolation: snapshot, delta cleanup, leak/residual detection.

The harness must be safe to run against a LIVE daemon with real user data: a
scenario that stores/recalls/forgets facts writes the durable memory DB, and a
truncate-all cleanup would wipe the user's own memory. So cleanup is a DELTA
operation — it removes ONLY what the run created — and it is doubly guarded so a
pre-existing row can never be deleted:

1. **Pre-run snapshot** (:func:`snapshot`) records a cutoff timestamp plus the
   baseline ids of every backing store: the `facts` rows, the `sessions` rows,
   and any artifact filenames, plus the chat-history length.
2. **Delta cleanup** (:func:`delta_cleanup`) deletes only rows whose creation
   time is at/after the cutoff AND whose id was NOT in the pre-run snapshot — the
   timestamp is the discriminator, the id set is the safety belt against clock
   skew. It then RE-QUERIES for run-era rows: any that survive are reported as
   ``CLEANUP INCOMPLETE`` (a cleanup that does not clean is itself a finding).
3. **Leak detection** (:func:`detect_leaks`) diffs a post-run snapshot against
   the pre-run one: any fact/session/file present after that was not present
   before — and survived cleanup — is a leak, reported, never swallowed.

The module talks to SQLite directly (no daemon/model import) so it is
fixture-testable and runs headless. The `facts` table soft-deletes via a
`deleted` flag for user-facing forgets; test-created rows are HARD-deleted here
because they are the harness's own litter, not user memory.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MemorySnapshot:
    """The baseline state of every backing store just before a run.

    ``cutoff`` is the timestamp the delta cleanup keys on: a row created at/after
    it is run-era. The id sets are the safety belt — a row whose id is in the
    snapshot is pre-existing and is NEVER deleted, even if its timestamp reads
    at/after the cutoff (clock skew, a re-touched row).
    """
    cutoff: float
    db_path: str
    fact_ids: frozenset[str] = frozenset()
    session_ids: frozenset[str] = frozenset()
    artifact_files: frozenset[str] = frozenset()
    chat_len: int = 0


@dataclass
class CleanupResult:
    """What delta cleanup removed, and whether any run-era row/file survived."""
    deleted_facts: int = 0
    deleted_sessions: int = 0
    deleted_files: int = 0
    residual_facts: list[str] = field(default_factory=list)
    residual_sessions: list[str] = field(default_factory=list)
    residual_files: list[str] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.residual_facts or self.residual_sessions or self.residual_files)

    def render(self) -> str:
        base = (f"cleanup: -{self.deleted_facts} facts, -{self.deleted_sessions} "
                f"sessions, -{self.deleted_files} files")
        if self.incomplete:
            return (base + " | CLEANUP INCOMPLETE — residual run-era rows: "
                    f"facts={self.residual_facts} sessions={self.residual_sessions} "
                    f"files={self.residual_files}")
        return base + " | clean"


@dataclass
class LeakReport:
    """Rows/files present after a run that were not present before (post - pre)."""
    new_facts: list[str] = field(default_factory=list)
    new_sessions: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)

    @property
    def leaked(self) -> bool:
        return bool(self.new_facts or self.new_sessions or self.new_files)

    def render(self) -> str:
        if not self.leaked:
            return "no leak: post-run snapshot == pre-run snapshot"
        return (f"LEAK: +{len(self.new_facts)} facts {self.new_facts} "
                f"+{len(self.new_sessions)} sessions {self.new_sessions} "
                f"+{len(self.new_files)} files {self.new_files}")


def _connect(db_path: str | Path) -> sqlite3.Connection | None:
    p = Path(db_path)
    if not p.exists():
        return None  # no memory DB yet — nothing to snapshot/clean
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def _table_ids(conn: sqlite3.Connection, table: str, id_col: str) -> set[str]:
    try:
        return {str(r[0]) for r in conn.execute(f"SELECT {id_col} FROM {table}")}
    except sqlite3.OperationalError:
        return set()  # table absent — a fresh/foreign DB; treated as empty baseline


def _artifact_files(artifact_dirs: list[str | Path] | None) -> set[str]:
    files: set[str] = set()
    for d in artifact_dirs or []:
        p = Path(d)
        if p.is_dir():
            files.update(str(f) for f in p.iterdir() if f.is_file())
    return files


def snapshot(db_path: str | Path, *, artifact_dirs: list[str | Path] | None = None,
             chat_len: int = 0, cutoff: float | None = None) -> MemorySnapshot:
    """Capture the baseline state before a run (or the post-run state for a diff).

    ``cutoff`` may be supplied for determinism (a test fixes it); otherwise it is
    the wall-clock now. Baselines are the current fact/session ids and artifact
    files, so a later cleanup/diff knows exactly what pre-existed.
    """
    ts = cutoff if cutoff is not None else time.time()
    conn = _connect(db_path)
    fact_ids: set[str] = set()
    session_ids: set[str] = set()
    if conn is not None:
        try:
            fact_ids = _table_ids(conn, "facts", "fact_id")
            session_ids = _table_ids(conn, "sessions", "session_id")
        finally:
            conn.close()
    return MemorySnapshot(
        cutoff=ts, db_path=str(db_path),
        fact_ids=frozenset(fact_ids), session_ids=frozenset(session_ids),
        artifact_files=frozenset(_artifact_files(artifact_dirs)), chat_len=chat_len)


def delta_cleanup(snap: MemorySnapshot, *,
                  artifact_dirs: list[str | Path] | None = None) -> CleanupResult:
    """Remove only what the run created; report anything run-era that survives.

    Deletes `facts` rows with ``created_at >= cutoff`` and `sessions` rows with
    ``started_at >= cutoff``, excluding any id present in the pre-run snapshot
    (the never-touch-pre-existing guard). Artifact files newer than the cutoff and
    not in the baseline are removed. Then re-queries for run-era rows/files: any
    survivor is a CLEANUP INCOMPLETE finding.
    """
    result = CleanupResult()
    conn = _connect(snap.db_path)
    if conn is not None:
        try:
            result.deleted_facts = _delete_delta(
                conn, "facts", "fact_id", "created_at", snap.cutoff, snap.fact_ids)
            result.deleted_sessions = _delete_delta(
                conn, "sessions", "session_id", "started_at", snap.cutoff, snap.session_ids)
            conn.commit()
            result.residual_facts = _residual(
                conn, "facts", "fact_id", "created_at", snap.cutoff, snap.fact_ids)
            result.residual_sessions = _residual(
                conn, "sessions", "session_id", "started_at", snap.cutoff, snap.session_ids)
        finally:
            conn.close()
    # Artifact files: remove run-era ones, then re-check for survivors.
    for f in _artifact_files(artifact_dirs) - snap.artifact_files:
        p = Path(f)
        try:
            if p.is_file() and p.stat().st_mtime >= snap.cutoff:
                p.unlink()
                result.deleted_files += 1
        except OSError:
            pass
    result.residual_files = sorted(
        f for f in _artifact_files(artifact_dirs) - snap.artifact_files
        if Path(f).exists() and Path(f).stat().st_mtime >= snap.cutoff)
    return result


def _delete_delta(conn: sqlite3.Connection, table: str, id_col: str, ts_col: str,
                  cutoff: float, baseline: frozenset[str]) -> int:
    try:
        rows = conn.execute(
            f"SELECT {id_col} FROM {table} WHERE {ts_col} >= ?", (cutoff,)).fetchall()
    except sqlite3.OperationalError:
        return 0
    to_delete = [str(r[0]) for r in rows if str(r[0]) not in baseline]
    for rid in to_delete:
        conn.execute(f"DELETE FROM {table} WHERE {id_col} = ?", (rid,))
    return len(to_delete)


def _residual(conn: sqlite3.Connection, table: str, id_col: str, ts_col: str,
              cutoff: float, baseline: frozenset[str]) -> list[str]:
    try:
        rows = conn.execute(
            f"SELECT {id_col} FROM {table} WHERE {ts_col} >= ?", (cutoff,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return sorted(str(r[0]) for r in rows if str(r[0]) not in baseline)


def detect_leaks(pre: MemorySnapshot, post: MemorySnapshot) -> LeakReport:
    """Diff a post-run snapshot against the pre-run one — anything new leaked."""
    return LeakReport(
        new_facts=sorted(post.fact_ids - pre.fact_ids),
        new_sessions=sorted(post.session_ids - pre.session_ids),
        new_files=sorted(post.artifact_files - pre.artifact_files),
    )

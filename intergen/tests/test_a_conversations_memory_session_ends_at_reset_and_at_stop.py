# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A conversation's memory session ends when the conversation resets and when the daemon stops.

THE CONTRACT. The sessions table is the record the welcome-back reply reads:
"what were we working on?" is answered from the most recent session that has
ENDED, with the topic it was summarised to and the time it ended. The daemon
starts a session for every conversation it serves and used to end none: a reset
replaced a conversation's session with a fresh one and left the old one open,
and a stop left every session open. A record that never closes says nothing
about the gap it was kept to describe, so the reply never had a session to read
and answered that there was no record.

So, on the store:
  * a session is ended ONCE: a second end leaves the first end's time and topic
    as they were;
  * an end the store refuses raises, and loses nothing: the turns the session's
    topic is summarised from are kept for the end that follows.

HOW THE CASES READ IT. Shipped code only, over a throwaway sqlite store; each
section below names the calls it drives. The sessions table is read back
through a SEPARATE sqlite connection to the store file, so what is asserted is
what was committed to disk and not the manager's own bookkeeping. A store that
refuses a write is made by sqlite itself, never by a stand-in: ``PRAGMA
query_only`` on the manager's own connection is a store that has gone
read-only.

NO LIVE TARGET. No daemon process, no bus, no socket, no model server.
"""

from __future__ import annotations

import sqlite3

import pytest

from intergen.memory import MemoryManager

# A turn whose text names the disk, and the topic a session holding it is
# summarised to (MemoryManager._auto_summarize_session).
DISK_TURN = "disk usage is 41%"
DISK_TOPIC = "checking disk space"


def _store(tmp_path) -> MemoryManager:
    """A MemoryManager on the throwaway store, as a start opens it."""
    return MemoryManager(str(tmp_path / "memory.db"))


def _sessions(tmp_path) -> dict[str, dict]:
    """Every sessions row as committed to the store file, read through a
    connection of this test's own."""
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.row_factory = sqlite3.Row
    try:
        return {row["session_id"]: dict(row)
                for row in conn.execute("SELECT * FROM sessions")}
    finally:
        conn.close()


# ── The end itself: MemoryManager.end_session ───────────────────────────────


def test_a_session_ended_a_second_time_keeps_its_first_end(tmp_path):
    """The second end of a session writes nothing: the end time and the topic
    the store holds are the first end's."""
    memory = _store(tmp_path)
    sid = memory.start_session()
    memory.record_turn(DISK_TURN, ["run_command"], session_id=sid)
    memory.end_session(session_id=sid)
    first = _sessions(tmp_path)[sid]
    assert first["ended_at"] is not None and first["topic"] == DISK_TOPIC, (
        f"control: the first end was not recorded as expected: {first}")

    memory.end_session("a summary written by a later end", session_id=sid)

    second = _sessions(tmp_path)[sid]
    assert (second["ended_at"], second["topic"]) == (
        first["ended_at"], first["topic"]), (
        "a session already ended was ended again: its end moved from "
        f"{first['ended_at']!r} ({first['topic']!r}) to "
        f"{second['ended_at']!r} ({second['topic']!r}). The record of when a "
        "conversation stopped, and what it was about, is overwritten by "
        "whatever ends it next.")


def test_an_end_the_store_refuses_raises_and_keeps_the_sessions_turns(tmp_path):
    """A refused end is not swallowed, and the turns the session's topic is
    summarised from survive it, so the end that follows records them."""
    memory = _store(tmp_path)
    sid = memory.start_session()
    memory.record_turn(DISK_TURN, ["run_command"], session_id=sid)
    conn = memory._get_conn()
    conn.execute("PRAGMA query_only = ON")
    with pytest.raises(sqlite3.OperationalError):
        memory.end_session(session_id=sid)
    conn.execute("PRAGMA query_only = OFF")
    assert _sessions(tmp_path)[sid]["ended_at"] is None, (
        "control: the store recorded an end it was set to refuse")

    memory.end_session(session_id=sid)

    row = _sessions(tmp_path)[sid]
    assert row["ended_at"] is not None, (
        "control: the end after the refused one was not recorded")
    assert row["topic"] == DISK_TOPIC, (
        f"the session ended with the topic {row['topic']!r}: the refused end "
        "had already discarded the turns its topic is summarised from, so the "
        "end that was written recorded a session about nothing, and a session "
        "with no topic is never offered back.")


def test_a_session_that_took_no_turns_is_not_offered_back(tmp_path):
    """Control, green at both ends: an ended session about nothing is never
    the one the welcome-back reply reads."""
    memory = _store(tmp_path)
    sid = memory.start_session()
    memory.end_session(session_id=sid)
    assert _sessions(tmp_path)[sid]["ended_at"] is not None
    assert memory.get_last_session() is None

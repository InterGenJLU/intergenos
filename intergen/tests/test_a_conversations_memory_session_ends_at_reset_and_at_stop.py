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
    topic is summarised from are kept for the end that follows;
  * a reset ends the session it replaces, recording the reason "reset", and a
    reset whose end the store refuses is reported and still made;
  * the welcome-back reply reads the session a reset ended.

HOW THE CASES READ IT. Shipped code only, over a throwaway sqlite store; each
section below names the calls it drives. The sessions table is read back
through a SEPARATE sqlite connection to the store file, so what is asserted is
what was committed to disk and not the manager's own bookkeeping. A store that
refuses a write is made by sqlite itself, never by a stand-in: ``PRAGMA
query_only`` on the manager's own connection is a store that has gone
read-only, and a trigger that refuses any write of ``ended_at`` is an end
refused while a new session can still be started.

NO LIVE TARGET. No daemon process, no bus, no socket, no model server.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from unittest import mock

import pytest

from intergen.interfaces.types import RouteResult, ToolResult
from intergen.memory import MemoryManager

# Turns whose text names the disk and the hostname, and the topics a session
# holding each is summarised to (MemoryManager._auto_summarize_session).
DISK_TURN = "disk usage is 41%"
DISK_TOPIC = "checking disk space"
HOST_TURN = "the hostname is set"
HOST_TOPIC = "checking system info"


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


# ── A reset ends the session it replaces ────────────────────────────────────
# ConversationRouter.reset_conversation_state: the call the desktop bus's
# ResetConversation makes for its conversation and the browser makes for a
# tab's ("new conversation", a switch to another chat). The router is built
# with the partial-router idiom of test_zz192_p4_06.py and is detached, as the
# daemon leaves it; turns are recorded through the real _record every
# deterministic route reaches, and the welcome-back reply is the real
# _try_memory.


def _router(memory: MemoryManager):
    """A router with no __init__ run, holding only what ``_record``, the reset
    and the memory rung read."""
    from intergen.router import ConversationRouter
    r = ConversationRouter.__new__(ConversationRouter)
    r._memory = memory
    r._metrics = None
    r._events = None
    r._embedder = None
    r._max_history = 20
    r._current_query_type = "general"
    r._last_semantic_score = None
    r._bound_conversation = None
    return r


def _take_a_turn(router, conv, text: str = DISK_TURN) -> None:
    """One deterministic turn served for ``conv``, named through
    ``bind_conversation`` as the frontends name it."""
    result = RouteResult(
        text=text,
        source="keyword",
        handled=True,
        tool_results=[ToolResult(call_id="call-1", name="run_command",
                                 content="ok")],
    )
    with router.bind_conversation(conv):
        router._record(result, time.monotonic(), "keyword")


def _refuse_every_end(tmp_path) -> None:
    """Arm the store to refuse any write of a session's end, through a trigger
    of sqlite's own, while every other write (a new session, a turn) lands."""
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.execute(
        "CREATE TRIGGER refuse_every_end BEFORE UPDATE OF ended_at ON sessions "
        "BEGIN SELECT RAISE(ABORT, 'this store refuses the end of a session'); "
        "END")
    conn.commit()
    conn.close()


class _Clock:
    """The memory module's clock, moved on by hand: the welcome-back reply is
    withheld for the first 60 seconds after a session ends, and these cases do
    not wait them out."""

    def __init__(self):
        self._offset = 0.0

    def time(self) -> float:
        return time.time() + self._offset

    def advance(self, seconds: float) -> None:
        self._offset += seconds


def test_a_reset_ends_the_session_it_replaces_with_the_reason_reset(tmp_path):
    """The conversation's turns were recorded into a session; the reset ends
    that session, with the reason and the topic of its turns, and starts the
    next."""
    memory = _store(tmp_path)
    router = _router(memory)
    conv = router.new_conversation()
    replaced = conv.memory_session_id
    _take_a_turn(router, conv)

    router.reset_conversation_state(conv)

    rows = _sessions(tmp_path)
    assert conv.memory_session_id not in (None, replaced), (
        "control: the reset did not give the conversation a new session")
    assert rows[conv.memory_session_id]["ended_at"] is None, (
        "control: the session the reset started is already ended")
    row = rows[replaced]
    assert row["ended_at"] is not None, (
        "the reset replaced the conversation's session and left the old one "
        "open: the conversation the person ended is recorded as still going, "
        "and the welcome-back reply has no session to read")
    assert (row.get("end_reason"), row["topic"]) == ("reset", DISK_TOPIC), (
        "the replaced session's end does not say why it ended or what it was "
        f"about: end_reason {row.get('end_reason')!r}, topic {row['topic']!r}")


def test_a_store_an_earlier_release_wrote_records_why_a_session_ended(tmp_path):
    """A store every earlier release created has no column for the reason; it
    is opened, a reset ends a session in it, and the reason is recorded."""
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.execute(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, topic TEXT NOT "
        "NULL, queries TEXT, tools_used TEXT, started_at REAL NOT NULL, "
        "ended_at REAL, turn_count INTEGER DEFAULT 0)")
    conn.execute(
        "INSERT INTO sessions (session_id, topic, started_at, ended_at, "
        "turn_count) VALUES ('an-earlier-one', 'checking network', 1.0, 2.0, 3)")
    conn.commit()
    conn.close()
    memory = _store(tmp_path)
    router = _router(memory)
    conv = router.new_conversation()
    replaced = conv.memory_session_id
    _take_a_turn(router, conv)

    router.reset_conversation_state(conv)

    rows = _sessions(tmp_path)
    earlier = rows["an-earlier-one"]
    assert (earlier["topic"], earlier["started_at"], earlier["ended_at"],
            earlier["turn_count"]) == ("checking network", 1.0, 2.0, 3), (
        f"control: opening the store changed a row it already held: {earlier}")
    assert rows[replaced]["ended_at"] is not None, (
        "the reset left the replaced session open on a store an earlier "
        "release wrote")
    assert rows[replaced].get("end_reason") == "reset", (
        "a store an earlier release wrote does not record why the session "
        f"ended: end_reason {rows[replaced].get('end_reason')!r}")


def test_a_reset_whose_end_the_store_refuses_reports_it_and_still_resets(
        tmp_path, caplog):
    """The end is refused; the refusal is reported by the session's name, and
    the conversation is still reset: its staged offer gone, a new session
    started."""
    memory = _store(tmp_path)
    router = _router(memory)
    conv = router.new_conversation()
    replaced = conv.memory_session_id
    _take_a_turn(router, conv)
    conv.pending_action_offer = ("systemctl restart sshd", "restart sshd",
                                 "sshd")
    _refuse_every_end(tmp_path)

    with caplog.at_level(logging.ERROR):
        router.reset_conversation_state(conv)

    assert conv.pending_action_offer is None, (
        "the reset stopped part-way: the ended conversation's staged offer "
        "survived it")
    assert conv.memory_session_id not in (None, replaced), (
        "the reset stopped part-way: the conversation was not given a new "
        "session")
    assert _sessions(tmp_path)[replaced]["ended_at"] is None, (
        "control: the store recorded an end it was armed to refuse")
    reported = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.ERROR and replaced in r.getMessage()]
    assert reported, (
        f"the store refused to end the session {replaced} and nothing "
        "reported it: the record stays open with no trace of why")


def test_the_welcome_back_reads_the_session_a_reset_ended(tmp_path):
    """After a reset, "what were we working on?" is answered from the session
    the reset ended."""
    from intergen import memory as memory_module
    memory = _store(tmp_path)
    router = _router(memory)
    conv = router.new_conversation()
    _take_a_turn(router, conv)
    clock = _Clock()
    with mock.patch.object(memory_module, "time", clock):
        router.reset_conversation_state(conv)
        clock.advance(61)
        with router.bind_conversation(conv):
            reply = router._try_memory("what were we working on?")
    assert reply.handled and reply.text.startswith("Welcome back."), (
        f"the welcome-back reply found no ended session: {reply.text!r}")
    assert DISK_TOPIC in reply.text, (
        f"the welcome-back reply does not name the session's topic: "
        f"{reply.text!r}")


def test_a_reset_leaves_every_other_conversations_session_open(tmp_path):
    """Control, green at both ends: a reset ends the conversation it is made
    for and no other; the tab beside it is still going."""
    memory = _store(tmp_path)
    router = _router(memory)
    reset_one, other = router.new_conversation(), router.new_conversation()
    _take_a_turn(router, reset_one)
    _take_a_turn(router, other, HOST_TURN)
    router.reset_conversation_state(reset_one)
    assert _sessions(tmp_path)[other.memory_session_id]["ended_at"] is None

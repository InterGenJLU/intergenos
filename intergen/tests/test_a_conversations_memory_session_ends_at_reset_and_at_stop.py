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
  * a session that has ended takes no more turns: a turn that arrives after the
    end is not counted, and is reported by the session's name;
  * an end the store refuses raises, and loses nothing: the turns the session's
    topic is summarised from are kept for the end that follows;
  * a reset ends the session it replaces, recording the reason "reset", and a
    reset whose end the store refuses is reported and still made;
  * a daemon stop ends every session that daemon started and has not ended,
    recording the reason "daemon stop", reports each one the store refuses, and
    still runs to its end;
  * the welcome-back reply reads the session that was ended, after a reset and
    at the next start.

HOW THE CASES READ IT. Shipped code only, over a throwaway sqlite store; each
section below names the calls it drives. The sessions table is read back
through a SEPARATE sqlite connection to the store file, so what is asserted is
what was committed to disk and not the manager's own bookkeeping. A store that
refuses a write is made by sqlite itself, never by a stand-in: ``PRAGMA
query_only`` on the manager's own connection is a store that has gone
read-only, and a trigger that refuses any write of ``ended_at`` is an end
refused while a new session can still be started.

NO LIVE TARGET. No daemon process, no bus, no socket, no model server. Where a
case needs the browser server's event loop and thread, they are real and run in
this process; the server object on them is a stand-in.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
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


# ── A daemon stop ends the sessions it started ──────────────────────────────
# InterGenDaemon.stop_service on a bare daemon built as
# test_dbus_stop_unexports_the_object.py builds one, carrying the memory
# manager its start makes and a router over it, with the desktop bus's
# conversation made the way its start makes it. The bus's ResetConversation is
# the daemon's own reset_conversation.


def _daemon(memory: MemoryManager, router):
    """A bare daemon holding what stop_service and reset_conversation read."""
    from intergen import dbus_daemon as dd
    d = object.__new__(dd.InterGenDaemon)
    d._bus = None
    d._reg_id = None
    d._owner_id = None
    d._running = True
    for attr in ("_web_server", "_web_thread", "_web_loop", "_state_cache",
                 "_watchdog", "_embed_watchdog", "_llama", "_embed_llama",
                 "_llm", "_matcher", "_tools", "_governance", "_health_agg"):
        setattr(d, attr, None)
    d._memory = memory
    d._conversation = router.new_conversation()
    d._router = router
    return d


def test_a_daemon_stop_ends_every_session_it_started_with_the_reason_daemon_stop(
        tmp_path):
    """The bus's conversation and a browser tab's both took turns; the stop
    ends both sessions, each with the reason and its own topic."""
    memory = _store(tmp_path)
    router = _router(memory)
    daemon = _daemon(memory, router)
    tab = router.new_conversation()
    _take_a_turn(router, daemon._conversation)
    _take_a_turn(router, tab, HOST_TURN)

    daemon.stop_service()

    rows = _sessions(tmp_path)
    for name, conv, topic in (("the desktop bus's", daemon._conversation,
                               DISK_TOPIC),
                              ("a browser tab's", tab, HOST_TOPIC)):
        row = rows[conv.memory_session_id]
        assert row["ended_at"] is not None, (
            f"the daemon stopped and {name} conversation's session is still "
            "open: the next start's welcome-back reply has nothing to read")
        assert (row.get("end_reason"), row["topic"]) == ("daemon stop",
                                                        topic), (
            f"{name} conversation's session ended without saying why or "
            f"what it was about: end_reason {row.get('end_reason')!r}, "
            f"topic {row['topic']!r}")


def test_a_session_a_reset_ended_is_not_ended_again_by_the_stop(tmp_path):
    """The bus's ResetConversation ended a session; the stop that follows
    leaves that end as it was and ends the session the reset started."""
    memory = _store(tmp_path)
    router = _router(memory)
    daemon = _daemon(memory, router)
    replaced = daemon._conversation.memory_session_id
    _take_a_turn(router, daemon._conversation)
    daemon.reset_conversation()
    at_reset = _sessions(tmp_path)[replaced]
    current = daemon._conversation.memory_session_id
    _take_a_turn(router, daemon._conversation, HOST_TURN)

    daemon.stop_service()

    rows = _sessions(tmp_path)
    assert at_reset["ended_at"] is not None and at_reset.get(
        "end_reason") == "reset", (
        f"the bus's reset did not end the session it replaced: {at_reset}")
    assert rows[replaced] == at_reset, (
        "the stop ended again a session the reset had already ended: "
        f"{at_reset} became {rows[replaced]}")
    assert rows[current]["ended_at"] is not None and rows[current].get(
        "end_reason") == "daemon stop", (
        f"the stop did not end the session the reset started: {rows[current]}")


def test_a_stop_whose_ends_the_store_refuses_reports_each_and_still_stops(
        tmp_path, caplog):
    """The store has gone read-only; the stop reports every session it could
    not end, by name, and still runs to its end."""
    memory = _store(tmp_path)
    router = _router(memory)
    daemon = _daemon(memory, router)
    tab = router.new_conversation()
    _take_a_turn(router, daemon._conversation)
    memory._get_conn().execute("PRAGMA query_only = ON")

    with caplog.at_level(logging.INFO):
        daemon.stop_service()

    assert daemon._router is None and any(
        r.getMessage() == "InterGen daemon stopped" for r in caplog.records), (
        "the stop did not run to its end once a session could not be ended")
    rows = _sessions(tmp_path)
    errors = [r.getMessage() for r in caplog.records
              if r.levelno >= logging.ERROR]
    for name, conv in (("the desktop bus's", daemon._conversation),
                       ("a browser tab's", tab)):
        sid = conv.memory_session_id
        assert rows[sid]["ended_at"] is None, (
            "control: the store recorded an end it was set to refuse")
        assert any(sid in message for message in errors), (
            f"the store refused to end {name} conversation's session {sid} "
            "at the stop and nothing reported it")


def test_a_daemon_stop_leaves_open_the_sessions_another_process_started(
        tmp_path):
    """Control, green at both ends: a store can be written by more than one
    process, and a stop ends only the sessions its own daemon started."""
    memory = _store(tmp_path)
    another = _store(tmp_path)
    theirs = another.start_session()
    another.record_turn(DISK_TURN, None, session_id=theirs)
    daemon = _daemon(memory, _router(memory))
    daemon.stop_service()
    assert _sessions(tmp_path)[theirs]["ended_at"] is None


def test_the_next_start_welcomes_the_person_back_to_the_session_the_stop_ended(
        tmp_path):
    """The daemon stops; the next start opens the same store, and "what were
    we working on?" is answered from the session the stop ended."""
    from intergen import memory as memory_module
    memory = _store(tmp_path)
    router = _router(memory)
    daemon = _daemon(memory, router)
    _take_a_turn(router, daemon._conversation)
    clock = _Clock()
    with mock.patch.object(memory_module, "time", clock):
        daemon.stop_service()
        clock.advance(61)
        # The next start: a new manager on the same store, a router over it
        # and the bus's conversation, as the daemon's start makes them.
        memory_next = _store(tmp_path)
        router_next = _router(memory_next)
        daemon_next = _daemon(memory_next, router_next)
        with router_next.bind_conversation(daemon_next._conversation):
            reply = router_next._try_memory("what were we working on?")
    assert reply.handled and reply.text.startswith("Welcome back."), (
        f"the next start's welcome-back reply found no ended session: "
        f"{reply.text!r}")
    assert DISK_TOPIC in reply.text, (
        f"the welcome-back reply does not name the session's topic: "
        f"{reply.text!r}")


# ── A daemon stop and the browser stop it orders ────────────────────────────
# InterGenDaemon.stop_service orders the browser server's stop on that server's
# own event loop, then ends the memory sessions. Here the browser server runs as
# the daemon runs it (_run_web_server): a real event loop run forever on a real
# thread, which the daemon's stop does not end. Only the server object on that
# loop is a stand-in, and only its stop(): it completes, stays pending until the
# case lets it go, or fails. A turn the browser server still serves after the
# stop was ordered is recorded through the real _record.


class _BrowserServerOnItsOwnLoop:
    """The browser server's loop and thread, real; its stop(), a stand-in."""

    def __init__(self, outcome: str):
        self.outcome = outcome
        self.stop_completed_at: float | None = None
        self.loop = asyncio.new_event_loop()
        self._release = asyncio.Event()
        self.thread = threading.Thread(target=self._serve, name="intergen-web",
                                       daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def stop(self) -> None:
        if self.outcome == "fails":
            raise RuntimeError("the browser server's stop failed")
        if self.outcome == "pending":
            await self._release.wait()
        self.stop_completed_at = time.time()

    def give_to(self, daemon) -> None:
        daemon._web_server = self
        daemon._web_loop = self.loop
        daemon._web_thread = self.thread

    def finish(self) -> None:
        """Let a pending stop go, then end the loop and its thread."""
        self.loop.call_soon_threadsafe(self._release.set)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05),
                                         self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if not self.thread.is_alive():
            self.loop.close()


def _a_stop_with_the_browser_stop(tmp_path, caplog, outcome: str):
    """A daemon whose bus conversation and a browser tab's each took a turn,
    stopped while its browser server's stop has the given outcome. Returns
    the router, the two conversations, the browser server and the log."""
    store = tmp_path / outcome
    store.mkdir()
    memory = _store(store)
    router = _router(memory)
    daemon = _daemon(memory, router)
    tab = router.new_conversation()
    _take_a_turn(router, daemon._conversation)
    _take_a_turn(router, tab, HOST_TURN)
    browser = _BrowserServerOnItsOwnLoop(outcome)
    browser.give_to(daemon)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        daemon.stop_service()
    messages = [(r.levelno, r.getMessage()) for r in caplog.records]
    return store, router, daemon._conversation, tab, browser, messages


def test_control_a_browser_stop_that_completes_comes_before_the_sessions_end(
        tmp_path, caplog):
    """Control, green at both ends: the browser stop completes; every session
    ends after it, with the reason "daemon stop", and the stop reports that the
    daemon stopped."""
    store, router, bus, tab, browser, messages = _a_stop_with_the_browser_stop(
        tmp_path, caplog, "completes")
    try:
        rows = _sessions(store)
        assert browser.stop_completed_at is not None, (
            "control: the browser stop never completed")
        for name, conv in (("the desktop bus's", bus), ("the tab's", tab)):
            row = rows[conv.memory_session_id]
            assert row["ended_at"] is not None and row.get(
                "end_reason") == "daemon stop", f"{name} session: {row}"
            assert row["ended_at"] >= browser.stop_completed_at, (
                f"{name} session ended at {row['ended_at']}, before the browser "
                f"stop completed at {browser.stop_completed_at}")
        assert any(m == "InterGen daemon stopped" for _, m in messages)
    finally:
        browser.finish()


# ── A session that has ended takes no more turns ────────────────────────────
# MemoryManager.record_turn, reached after the session's end: a routing thread
# runs on past its connection's close or past the daemon's stop, and delivers
# its turn to a session that is already closed.


def test_a_turn_recorded_after_its_session_ended_is_not_counted_and_is_reported(
        tmp_path, caplog):
    """The ended record is left exactly as it was, and the late turn is
    reported by the session's name."""
    memory = _store(tmp_path)
    sid = memory.start_session()
    memory.record_turn(DISK_TURN, ["run_command"], session_id=sid)
    memory.end_session(session_id=sid, reason="reset")
    at_end = _sessions(tmp_path)[sid]
    assert at_end["ended_at"] is not None and at_end["turn_count"] == 1, (
        f"control: the session was not ended as expected: {at_end}")

    with caplog.at_level(logging.WARNING):
        memory.record_turn(HOST_TURN, ["run_command"], session_id=sid)

    after = _sessions(tmp_path)[sid]
    assert after == at_end, (
        "a turn recorded after its session ended changed the ended record: "
        f"{at_end} became {after}")
    reported = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and sid in r.getMessage()]
    assert reported, (
        f"a turn arrived for the ended session {sid} and nothing reported it")


def test_a_turn_served_after_the_stop_ended_the_sessions_is_not_counted_on_them(
        tmp_path, caplog):
    """The browser stop completes and the stop ends every session; a turn
    delivered afterwards for a browser tab is not counted on the tab's ended
    session, and is reported."""
    store, router, bus, tab, browser, messages = _a_stop_with_the_browser_stop(
        tmp_path, caplog, "completes")
    try:
        at_stop = _sessions(store)[tab.memory_session_id]
        assert at_stop["ended_at"] is not None, (
            f"control: the stop did not end the tab's session: {at_stop}")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            _take_a_turn(router, tab, DISK_TURN)
        after = _sessions(store)[tab.memory_session_id]
        assert after == at_stop, (
            "a turn delivered after the stop ended the tab's session was "
            f"counted on it: {at_stop} became {after}")
        reported = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.WARNING
                    and tab.memory_session_id in r.getMessage()]
        assert reported, (
            "a turn arrived for the tab's ended session and nothing reported it")
    finally:
        browser.finish()

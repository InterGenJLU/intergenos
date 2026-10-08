# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The rest of a conversation's memory session is its own too.

The sealed case beside this module (test_zz192_p4_06.py) measures the turn
count: a turn was counted against ``MemoryManager._current_session_id``, the
session whichever conversation started LAST, never against the bound
conversation's own ``memory_session_id``. These cases hold the two other places
the same record lived manager-wide:

* A conversation the daemon or a browser connection makes with
  ``ConversationRouter.new_conversation()`` had NO session until somebody
  pressed "new conversation" in it, so its turns could only ever be counted
  against another conversation's. Every conversation now gets its own session
  where it is made.
* The queries and tools a session is summarised from when it ends were one
  list for the whole manager, so ending one conversation's session wrote every
  conversation's turns into it.

The router is the partial-router idiom the sealed case uses, holding only what
``_record`` and ``new_conversation`` read, and DETACHED as the daemon leaves it.
The store is a throwaway sqlite file, read back through a connection of the
test's own. No daemon, socket, model or network.
"""

from __future__ import annotations

import sqlite3
import time

from intergen.interfaces.types import RouteResult, ToolResult
from intergen.memory import MemoryManager


def _store(tmp_path) -> MemoryManager:
    return MemoryManager(str(tmp_path / "memory.db"))


def _router(memory: MemoryManager):
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


def _take_a_turn(router, conv, text: str, tool: str = "run_command") -> None:
    result = RouteResult(text=text, source="keyword", handled=True,
                         tool_results=[ToolResult(call_id="call-1", name=tool,
                                                  content="ok")])
    with router.bind_conversation(conv):
        router._record(result, time.monotonic(), "keyword")


def _rows(tmp_path) -> dict[str, dict]:
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.row_factory = sqlite3.Row
    try:
        return {row["session_id"]: dict(row)
                for row in conn.execute("SELECT * FROM sessions")}
    finally:
        conn.close()


# ── red before the correction ───────────────────────────────────────────────

def test_a_conversation_has_its_own_session_from_the_moment_it_is_made(tmp_path):
    """The daemon's bus conversation and each browser connection's are made by
    new_conversation(); each must hold a session of its own from the start."""
    router = _router(_store(tmp_path))

    bus = router.new_conversation()
    browser = router.new_conversation()

    assert bus.memory_session_id is not None, (
        "a conversation made by new_conversation() holds no memory session, so "
        "every turn it takes is counted against some other conversation's")
    assert browser.memory_session_id is not None
    assert bus.memory_session_id != browser.memory_session_id, (
        "two conversations were handed one memory session")
    rows = _rows(tmp_path)
    assert bus.memory_session_id in rows and browser.memory_session_id in rows, (
        f"the sessions are not in the store: {sorted(rows)}")


def test_a_turn_on_a_conversation_never_reset_is_counted_on_its_own_session(tmp_path):
    """No one pressed "new conversation": the two conversations are exactly as
    the daemon and the browser server make them."""
    router = _router(_store(tmp_path))
    bus = router.new_conversation()
    browser = router.new_conversation()

    _take_a_turn(router, bus, "disk usage is 41%")
    _take_a_turn(router, bus, "uptime is 3 days")
    _take_a_turn(router, browser, "the battery is at 88%")

    rows = _rows(tmp_path)
    counted = {name: rows.get(conv.memory_session_id, {}).get("turn_count")
               for name, conv in (("bus", bus), ("browser", browser))}
    assert counted == {"bus": 2, "browser": 1}, (
        f"the store counted {counted} for two turns on the bus conversation and "
        "one on the browser's")


def test_ending_a_session_summarises_only_its_own_turns(tmp_path):
    """The summary of a session is built from that session's turns, not from
    every turn the shared manager has seen."""
    memory = _store(tmp_path)
    router = _router(memory)
    first = router.new_conversation()
    router.reset_conversation_state(first)
    second = router.new_conversation()
    router.reset_conversation_state(second)

    _take_a_turn(router, first, "disk usage is 41%", tool="disk_usage")
    _take_a_turn(router, second, "the battery is at 88%", tool="battery")

    memory.end_session()        # the session started last: the second's

    row = _rows(tmp_path)[second.memory_session_id]
    assert row["queries"] == "the battery is at 88%", (
        "ending the second conversation's session wrote another conversation's "
        f"turns into it: queries {row['queries']!r}")
    assert row["tools_used"] == "battery", (
        f"and another conversation's tools: tools_used {row['tools_used']!r}")
    assert row["topic"] == "general queries", (
        "its topic was summarised from another conversation's turns: "
        f"{row['topic']!r}")


# ── controls: green before and after ────────────────────────────────────────

def test_control_the_callers_that_name_no_session_record_into_the_last_one(tmp_path):
    """The test runner and the declarative suite start a session and record
    without naming it; that keeps meaning the session they just started."""
    memory = _store(tmp_path)
    sid = memory.start_session()
    memory.record_turn("checked disk space", ["run_command"])
    memory.record_turn("checked hostname", ["run_command"])
    memory.end_session("checking disk space and system info")

    row = _rows(tmp_path)[sid]
    assert row["turn_count"] == 2
    assert row["queries"] == "checked disk space\nchecked hostname"
    assert row["tools_used"] == "run_command"
    assert row["topic"] == "checking disk space and system info"
    assert row["ended_at"] is not None


def test_control_a_reset_gives_the_conversation_a_new_session(tmp_path):
    router = _router(_store(tmp_path))
    conv = router.new_conversation()
    router.reset_conversation_state(conv)
    before = conv.memory_session_id
    router.reset_conversation_state(conv)

    assert conv.memory_session_id is not None
    assert conv.memory_session_id != before

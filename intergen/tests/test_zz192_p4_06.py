# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A turn is recorded against the memory session of the conversation it belongs to.

THE CONTRACT. ``ConversationState.memory_session_id`` is documented, field by
field, as "the long-term memory session this conversation records into"
(intergen/conversation_state.py). One daemon serves every browser tab and the
desktop bus from ONE router and ONE MemoryManager, and each of those surfaces
holds its own ConversationState — so that field is the only thing that can say
which conversation a recorded turn came from.

WHAT THESE CASES DRIVE. Shipped code only: the real ``MemoryManager`` over a
throwaway sqlite store, the real ``ConversationRouter.reset_conversation_state``
(the method ``web_server._handle_new_session`` calls when a tab presses "new
conversation"), the real ``bind_conversation`` the web server binds every turn
with, and the real ``ConversationRouter._record`` that every deterministic route
reaches. The router is built with the partial-router idiom used by
test_conversation_isolation.py and test_offer_accept.py, with only the
attributes ``_record`` touches placed on it, and it is DETACHED
(``_bound_conversation = None``) exactly as the daemon leaves it.

The sessions table is read back through a SEPARATE sqlite connection to the
store file, so what is asserted is what was committed to disk and not the
manager's own in-process bookkeeping.

NO LIVE TARGET. No daemon, no socket, no model server, no browser, no display,
no network. The two-real-WebSocket-clients leg belongs to the installed-system
gate tier.
"""

from __future__ import annotations

import sqlite3
import time

from intergen.conversation_state import new_conversation_state
from intergen.interfaces.types import RouteResult, ToolResult
from intergen.memory import MemoryManager


def _store(tmp_path) -> MemoryManager:
    """A MemoryManager on its own throwaway store, holding nothing else."""
    return MemoryManager(str(tmp_path / "memory.db"))


def _router(memory: MemoryManager):
    """A router with no __init__ run, holding only what ``_record`` reads.

    ``_bound_conversation`` is None because that is what the daemon does
    (``detach_conversation``): on a router serving several conversations every
    turn must name the conversation it belongs to.
    """
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


def _start_conversation(router):
    """A fresh conversation with its own memory session, started the way the
    browser server starts one: ``new_conversation()`` for the connection, then
    ``reset_conversation_state()`` when the person opens a new conversation in
    it (web_server._handle_new_session)."""
    conv = router.new_conversation()
    router.reset_conversation_state(conv)
    return conv


def _take_a_turn(router, conv, text: str) -> None:
    """One deterministic turn served for ``conv``.

    The conversation is named through ``bind_conversation`` — the one seam the
    web server uses to say which conversation a turn belongs to — and the turn
    is recorded through the real ``_record``, which every deterministic route
    (keyword, semantic, memory, system_map ...) reaches.
    """
    result = RouteResult(
        text=text,
        source="keyword",
        handled=True,
        tool_results=[ToolResult(call_id="call-1", name="run_command",
                                 content="ok")],
    )
    with router.bind_conversation(conv):
        router._record(result, time.monotonic(), "keyword")


def _session_rows(tmp_path) -> dict[str, dict]:
    """Every sessions row as committed to the store file, read through a
    connection of this test's own."""
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.row_factory = sqlite3.Row
    try:
        return {row["session_id"]: dict(row)
                for row in conn.execute("SELECT * FROM sessions")}
    finally:
        conn.close()


def test_a_turn_is_recorded_against_its_own_conversations_memory_session(tmp_path):
    """Tab A takes three turns, tab B presses "new conversation", tab A takes a
    fourth. All four turns are tab A's and none of them are tab B's."""
    memory = _store(tmp_path)
    router = _router(memory)

    tab_a = _start_conversation(router)
    for text in ("disk usage is 41%", "the hostname is set", "uptime is 3 days"):
        _take_a_turn(router, tab_a, text)

    # The other tab starts a conversation of its own. Nothing about tab A ended.
    tab_b = _start_conversation(router)

    # ... and tab A, still open, takes another turn.
    _take_a_turn(router, tab_a, "memory is 12 GB free")

    rows = _session_rows(tmp_path)
    assert tab_a.memory_session_id in rows, (
        "control: tab A's conversation has no session row at all")
    assert tab_b.memory_session_id in rows, (
        "control: tab B's conversation has no session row at all")
    assert tab_a.memory_session_id != tab_b.memory_session_id, (
        "control: the two conversations were handed the same memory session")

    assert rows[tab_a.memory_session_id]["turn_count"] == 4, (
        "tab A took four turns; its own memory session counted "
        f"{rows[tab_a.memory_session_id]['turn_count']}. A turn is recorded "
        "against MemoryManager._current_session_id — whichever conversation "
        "reset last — and never against the conversation that was bound, so "
        "ConversationState.memory_session_id records nothing.")
    assert rows[tab_b.memory_session_id]["turn_count"] == 0, (
        "tab B took no turns; its memory session counted "
        f"{rows[tab_b.memory_session_id]['turn_count']} — tab A's turns landed "
        "on tab B's row.")


def test_two_live_conversations_each_count_only_their_own_turns(tmp_path):
    """Two conversations open at once, one turn each: one turn on each row."""
    memory = _store(tmp_path)
    router = _router(memory)

    first = _start_conversation(router)
    second = _start_conversation(router)

    _take_a_turn(router, first, "the disk is 41% full")
    _take_a_turn(router, second, "the battery is at 88%")

    rows = _session_rows(tmp_path)
    counted = {
        "first": rows[first.memory_session_id]["turn_count"],
        "second": rows[second.memory_session_id]["turn_count"],
    }
    assert counted == {"first": 1, "second": 1}, (
        f"one turn was served for each conversation; the store counted {counted}. "
        "Both turns were attributed to the conversation that started last.")


def test_control_a_single_conversation_counts_its_turns(tmp_path):
    """CONTROL — passes on this tree. With one conversation on the machine the
    manager's current session and the conversation's session are the same row,
    so the instrument above is measuring attribution and not a broken counter."""
    memory = _store(tmp_path)
    router = _router(memory)

    only = _start_conversation(router)
    _take_a_turn(router, only, "disk usage is 41%")
    _take_a_turn(router, only, "uptime is 3 days")

    rows = _session_rows(tmp_path)
    assert rows[only.memory_session_id]["turn_count"] == 2

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

A turn the model answers in the browser is routed with decide_only, and the
router returns only its decision: the web server generates the answer, streams
it and counts the turn itself. The last case drives that through the web
server's own message handler over a full router (its section says how).

The sessions table is read back through a SEPARATE sqlite connection to the
store file, so what is asserted is what was committed to disk and not the
manager's own in-process bookkeeping.

NO LIVE TARGET. No daemon, no socket, no model server, no browser, no display,
no network. The two-real-WebSocket-clients leg belongs to the installed-system
gate tier.
"""

from __future__ import annotations

import asyncio
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


# ── A streamed model turn, served through the web server's stream path ──────
# The web server routes every chat turn with decide_only=True: a route that
# needs the model returns only its decision, and the answer is generated and
# streamed by WebServer._stream_llm_response. This case drives the SHIPPED
# message handler, _handle_client_message, over a real ConversationRouter on
# the real tool registry (tool offering locked, as the daemon locks it) and one
# MemoryManager on the throwaway store, the router detached as the daemon
# leaves it. Stand-ins, each named: the model's entry points (a fixed answer)
# and the connection's socket (it keeps every frame sent). No socket, model
# server or network is reached.


class _RecordingSocket:
    """The connection's socket: keeps every frame the server sends."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)


_MODEL_ANSWER = "Linux was first released in 1991."


def _web_server_over_a_real_router(tmp_path):
    """The browser server as the daemon builds it, over a real router and the
    throwaway store; only the model and the transcript directory differ."""
    from intergen.interfaces.types import LLMResponse
    from intergen.llm import LLMRouter
    from intergen.router import ConversationRouter
    from intergen.semantic import SemanticMatcher
    from intergen.session_manager import SessionManager
    from intergen.tool_registry import ToolRegistry
    from intergen.web_server import WebServer

    memory = _store(tmp_path)
    registry = ToolRegistry()
    registry.discover_tools()
    registry.set_tool_offering_locked(True)
    llm = LLMRouter(config=None)

    def _chat(messages, **kwargs):
        return LLMResponse(text=_MODEL_ANSWER, model="stand-in")

    def _stream(messages, *args, **kwargs):
        yield _MODEL_ANSWER

    llm.chat, llm.stream, llm.stream_with_tools = _chat, _stream, _stream
    router = ConversationRouter(tool_registry=registry,
                                semantic_matcher=SemanticMatcher(embedder=None),
                                llm=llm, lock_dispatch=True, memory=memory)
    router.detach_conversation()
    server = WebServer(router=router, llm=llm, tools=None, governance=None,
                       memory=memory)
    server._sessions = SessionManager(sessions_dir=tmp_path / "transcripts")
    return router, server


def test_a_streamed_model_turn_is_counted_once_in_its_own_conversations_session(
        tmp_path):
    """A browser tab asks a question the model answers; the answer is streamed
    to it. That turn is counted once, in the tab's own memory session, and in
    no other conversation's."""
    from intergen.web_server import ConnectionContext
    router, server = _web_server_over_a_real_router(tmp_path)
    tab = ConnectionContext(client_id="web_streamed", source_interface="web",
                            ws=_RecordingSocket(),
                            conversation=router.new_conversation())
    other = router.new_conversation()

    asyncio.run(server._handle_client_message(
        tab, {"type": "message",
              "content": "What year was Linux first released?"}))

    starts = [m for m in tab.ws.sent if m.get("type") == "stream_start"]
    ends = [m for m in tab.ws.sent if m.get("type") == "stream_end"]
    assert (len(starts) == 1 and starts[0].get("source") in (
        "llm_freeform", "llm_tools") and len(ends) == 1
            and _MODEL_ANSWER in ends[0].get("full_response", "")), (
        "control: the turn was not served through the stream path: "
        f"{[(m.get('type'), m.get('source')) for m in tab.ws.sent]}")
    rows = _session_rows(tmp_path)
    assert rows[other.memory_session_id]["turn_count"] == 0, (
        "the streamed turn was counted on another conversation's session: "
        f"{rows[other.memory_session_id]['turn_count']}")
    own = tab.conversation.memory_session_id
    assert rows[own]["turn_count"] == 1, (
        "the tab was served one streamed model turn; its own memory session "
        f"counted {rows[own]['turn_count']}. The stream path "
        "records no turn, so a conversation the model answered is summarised "
        "at its end as if it had not taken that turn.")

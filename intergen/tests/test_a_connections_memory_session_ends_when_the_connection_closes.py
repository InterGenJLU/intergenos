# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A browser connection's memory session ends when the connection closes.

THE CONTRACT. Every browser connection is a conversation of its own, made with
the router's ``new_conversation()``, which starts that conversation's memory
session. A reset ends the session and starts the next one ("new conversation",
a switch to another chat), and a daemon stop ends every session the daemon
started. A tab closed without a reset ended nothing: its session stayed open
until the daemon stopped and was then ended with the stop's time and reason, so
the record said the conversation went on for as long as the daemon ran.

So, when a connection closes:
  * its conversation's memory session is ended with the reason "connection
    closed" and the close's time, through ``MemoryManager.end_session`` — once:
    a session a reset already ended keeps that end;
  * an end the store refuses is reported by the session's name, the connection
    is still let go, and the turns the session is summarised from are kept for
    the end that follows;
  * a connection the browser server's own stop closes records the stop's
    reason, "daemon stop", the reason the daemon's stop records for every other
    session, so the record does not depend on which of the two ends runs first;
  * a turn still being routed when the connection closes is not counted on the
    session the close ended, and is reported by the session's name;
  * every other connection's session is left as it is.

HOW THE CASES DRIVE IT. The shipped ``WebServer``, started on a free port of
the loopback interface, over a real ``ConversationRouter`` on the real tool
registry (tool offering locked, as the daemon locks it) and one
``MemoryManager`` on a throwaway store, the router detached as the daemon leaves
it. A real WebSocket client connects with the server's token, takes memory
turns (which no model answers) and closes. Stand-ins, each named: the model's
entry points (a fixed answer; a memory turn never reaches them, and each case
holds that no call was made), the token file and the transcript directory (both
in the case's own directory). The sessions table is read back through a sqlite
connection of the case's own, so what is asserted is what reached the store.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading
import time
from unittest import mock

import aiohttp

from intergen.memory import MemoryManager

_TOKEN = "a-token-for-these-cases-only"
_MODEL_ANSWER = "(a model's answer, withheld: no model is reached by these cases)"

# Memory turns, and the topics a session holding each is summarised to
# (MemoryManager._auto_summarize_session reads the recorded reply).
HOST_TURN = "remember that my hostname is igos-close"
HOST_TOPIC = "checking system info"
DISK_TURN = "remember that my backup disk is /dev/sdb1"
DISK_TOPIC = "checking disk space"


def _sessions(tmp_path) -> dict[str, dict]:
    """Every sessions row as committed to the store file, read through a
    connection of this case's own."""
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.row_factory = sqlite3.Row
    try:
        return {row["session_id"]: dict(row)
                for row in conn.execute("SELECT * FROM sessions")}
    finally:
        conn.close()


def _refuse_every_end(tmp_path) -> None:
    """Arm the store to refuse any write of a session's end, through a trigger
    of sqlite's own, while every other write lands."""
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.execute(
        "CREATE TRIGGER refuse_every_end BEFORE UPDATE OF ended_at ON sessions "
        "BEGIN SELECT RAISE(ABORT, 'this store refuses the end of a session'); "
        "END")
    conn.commit()
    conn.close()


def _accept_ends_again(tmp_path) -> None:
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.execute("DROP TRIGGER refuse_every_end")
    conn.commit()
    conn.close()


def _real_router(memory: MemoryManager, model_calls: list):
    """The router as the daemon builds it, over ``memory``; only the model's
    entry points are stand-ins, and each call to one is recorded."""
    from intergen.interfaces.types import LLMResponse
    from intergen.llm import LLMRouter
    from intergen.router import ConversationRouter
    from intergen.semantic import SemanticMatcher
    from intergen.tool_registry import ToolRegistry

    registry = ToolRegistry()
    registry.discover_tools()
    registry.set_tool_offering_locked(True)
    llm = LLMRouter(config=None)

    def _chat(messages, **kwargs):
        model_calls.append("chat")
        return LLMResponse(text=_MODEL_ANSWER, model="stand-in")

    def _stream(messages, *args, **kwargs):
        model_calls.append("stream")
        yield _MODEL_ANSWER

    llm.chat, llm.stream, llm.stream_with_tools = _chat, _stream, _stream
    router = ConversationRouter(tool_registry=registry,
                                semantic_matcher=SemanticMatcher(embedder=None),
                                llm=llm, lock_dispatch=True, memory=memory)
    router.detach_conversation()
    return router


@contextlib.asynccontextmanager
async def _a_server(tmp_path, model_calls: list):
    """The browser server, started and ready on a free loopback port."""
    from intergen import web_server
    from intergen.session_manager import SessionManager

    token = tmp_path / "web-token"
    token.write_text(_TOKEN)
    memory = MemoryManager(str(tmp_path / "memory.db"))
    router = _real_router(memory, model_calls)
    server = web_server.WebServer(host="127.0.0.1", port=0, router=router,
                                  llm=router._llm, tools=None, governance=None,
                                  memory=memory)
    server._sessions = SessionManager(sessions_dir=tmp_path / "transcripts")
    with mock.patch.object(web_server, "TOKEN_PATH", token):
        assert await server.start(), "control: the server did not bind"
        server.mark_ready()
        try:
            yield server, memory
        finally:
            if server.running:
                await server.stop()


async def _connect(http, server):
    """A browser tab's connection: returns the socket and the client id the
    server named it by."""
    host, port = server._runner.addresses[0][:2]
    ws = await http.ws_connect(f"http://{host}:{port}/ws",
                               protocols=("intergen", f"bearer.{_TOKEN}"))
    connected = await ws.receive_json(timeout=10)
    assert connected.get("type") == "connected", connected
    return ws, connected["client_id"]


async def _take_a_turn(ws, text: str) -> dict:
    """Send one message; return the frame that answers it."""
    await ws.send_json({"type": "message", "content": text})
    while True:
        frame = await ws.receive_json(timeout=20)
        if frame.get("type") in ("response", "stream_end", "error"):
            return frame


async def _new_conversation(ws) -> dict:
    """Press "new conversation" in the tab: the server resets the tab's
    conversation and answers with the new chat's id."""
    await ws.send_json({"type": "new_session"})
    while True:
        frame = await ws.receive_json(timeout=10)
        if frame.get("type") in ("session_created", "error"):
            return frame


async def _let_go(server, client_id: str) -> bool:
    """Wait until the server's close path has let the connection go."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if client_id not in server._connections:
            return True
        await asyncio.sleep(0.02)
    return False


def _session_of(server, client_id: str) -> str:
    return server._connections[client_id].conversation.memory_session_id


# ── The close ends the session ───────────────────────────────────────────────


def test_a_connection_closed_without_a_reset_ends_its_session_as_connection_closed(
        tmp_path):
    """A tab takes a turn and is closed. Its session is ended at the close, with
    the reason "connection closed", the close's time and its turn's topic."""
    model_calls: list = []

    async def scenario():
        async with _a_server(tmp_path, model_calls) as (server, _memory):
            async with aiohttp.ClientSession() as http:
                ws, client_id = await _connect(http, server)
                sid = _session_of(server, client_id)
                answer = await _take_a_turn(ws, HOST_TURN)
                closing = time.time()
                await ws.close()
                let_go = await _let_go(server, client_id)
                closed = time.time()
                return sid, answer, let_go, closing, closed, _sessions(tmp_path)

    sid, answer, let_go, closing, closed, rows = asyncio.run(scenario())
    assert answer.get("source") == "memory" and not model_calls, (
        f"control: the turn was not a memory turn: {answer}, model {model_calls}")
    assert let_go, "control: the server never let the closed connection go"
    row = rows[sid]
    assert row["ended_at"] is not None, (
        "the tab was closed without a reset and its memory session is still "
        "open: it stays open until the daemon stops, and is then recorded as a "
        "conversation that went on for as long as the daemon ran")
    assert row["end_reason"] == "connection closed", (
        f"the session ended without saying why: {row['end_reason']!r}")
    assert closing <= row["ended_at"] <= closed, (
        f"the session's end, {row['ended_at']}, is not the close's time "
        f"({closing} to {closed})")
    assert (row["topic"], row["turn_count"]) == (HOST_TOPIC, 1), (
        f"the end does not record the tab's turn: {row}")


def test_a_connection_closed_after_a_reset_ends_only_the_session_the_reset_started(
        tmp_path):
    """The tab presses "new conversation" (the reset ends the first session),
    takes another turn and is closed: the reset's end stays as it was and the
    close ends the second session, once."""
    model_calls: list = []

    async def scenario():
        async with _a_server(tmp_path, model_calls) as (server, _memory):
            async with aiohttp.ClientSession() as http:
                ws, client_id = await _connect(http, server)
                first = _session_of(server, client_id)
                await _take_a_turn(ws, HOST_TURN)
                created = await _new_conversation(ws)
                second = _session_of(server, client_id)
                at_reset = _sessions(tmp_path)
                await _take_a_turn(ws, DISK_TURN)
                await ws.close()
                let_go = await _let_go(server, client_id)
                return first, second, created, at_reset, let_go, _sessions(tmp_path)

    first, second, created, at_reset, let_go, rows = asyncio.run(scenario())
    assert created.get("type") == "session_created" and not model_calls, (
        f"control: the tab's new conversation was not made: {created}")
    assert second not in (None, first), (
        "control: the reset did not give the tab a new session")
    assert at_reset[first]["end_reason"] == "reset", (
        f"control: the reset did not end the first session: {at_reset[first]}")
    assert let_go, "control: the server never let the closed connection go"
    assert rows[first] == at_reset[first], (
        "the close ended again a session the reset had already ended: "
        f"{at_reset[first]} became {rows[first]}")
    row = rows[second]
    assert row["ended_at"] is not None and row["end_reason"] == "connection closed", (
        "the close did not end the session the reset started: "
        f"ended {row['ended_at']!r}, reason {row['end_reason']!r}")
    assert (row["topic"], row["turn_count"]) == (DISK_TOPIC, 1), (
        f"the second session's end does not record its own turn: {row}")


def test_an_end_the_store_refuses_at_the_close_is_reported_and_keeps_the_turns(
        tmp_path, caplog):
    """The store refuses the end: the close reports it by the session's name and
    still lets the connection go, and the turns survive for the end that
    follows (the daemon's stop, here)."""
    model_calls: list = []

    async def scenario():
        async with _a_server(tmp_path, model_calls) as (server, memory):
            async with aiohttp.ClientSession() as http:
                ws, client_id = await _connect(http, server)
                sid = _session_of(server, client_id)
                await _take_a_turn(ws, HOST_TURN)
                _refuse_every_end(tmp_path)
                with caplog.at_level(logging.ERROR):
                    await ws.close()
                    let_go = await _let_go(server, client_id)
                at_close = _sessions(tmp_path)[sid]
                _accept_ends_again(tmp_path)
                memory.end_open_sessions("daemon stop")
                return sid, let_go, at_close, _sessions(tmp_path)[sid]

    sid, let_go, at_close, after = asyncio.run(scenario())
    assert not model_calls, f"control: a model was called: {model_calls}"
    assert let_go, (
        "the store refused the session's end and the close stopped there: the "
        "connection was never let go")
    assert at_close["ended_at"] is None, (
        "control: the store recorded an end it was armed to refuse")
    reported = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.ERROR and sid in r.getMessage()]
    assert reported, (
        f"the store refused to end the session {sid} at its connection's close "
        "and nothing reported it")
    assert (after["end_reason"], after["topic"]) == ("daemon stop", HOST_TOPIC), (
        "the refused end at the close lost the turns the session is summarised "
        f"from: the end that followed recorded {after}")


def test_a_connection_the_servers_own_stop_closes_records_the_stops_reason(
        tmp_path):
    """The daemon stops the browser server while a tab is open. The stop closes
    the tab's connection, and the session is ended at that close with the stop's
    reason, "daemon stop"."""
    model_calls: list = []

    async def scenario():
        async with _a_server(tmp_path, model_calls) as (server, _memory):
            async with aiohttp.ClientSession() as http:
                ws, client_id = await _connect(http, server)
                sid = _session_of(server, client_id)
                await _take_a_turn(ws, HOST_TURN)
                # The tab keeps reading, as a browser does, so it answers the
                # server's close.
                reading = asyncio.ensure_future(ws.receive())
                stopping = time.time()
                await server.stop()
                stopped = time.time()
                await asyncio.wait({reading}, timeout=5)
                return sid, stopping, stopped, _sessions(tmp_path)[sid]

    sid, stopping, stopped, row = asyncio.run(scenario())
    assert not model_calls, f"control: a model was called: {model_calls}"
    assert row["ended_at"] is not None, (
        "the browser server's stop closed the tab's connection and left its "
        "session open: it is ended only if the daemon's own end comes after the "
        "close, and then the reason depends on which ran first")
    assert row["end_reason"] == "daemon stop", (
        f"a connection the stop closed records {row['end_reason']!r}")
    assert stopping <= row["ended_at"] <= stopped, (
        f"the session's end, {row['ended_at']}, is not inside the stop "
        f"({stopping} to {stopped})")
    assert row["topic"] == HOST_TOPIC, f"the end lost the tab's turn: {row}"


def test_a_turn_still_being_routed_when_its_connection_closes_is_not_counted_on_the_ended_session(
        tmp_path, caplog):
    """The tab sends a message and is closed while that turn is still being
    routed: route() runs in a worker thread, which a close cannot stop (one
    waiting on a review card waits up to five minutes). The close ends the
    session; the turn routed after it is not counted on that ended session, and
    is reported by the session's name. The routing is held by a stand-in that
    waits for the case's word and then calls the real route()."""
    model_calls: list = []

    async def scenario():
        async with _a_server(tmp_path, model_calls) as (server, _memory):
            router = server._router
            real_route = router.route
            routing, release, routed = (threading.Event(), threading.Event(),
                                        threading.Event())

            def held_route(*args, **kwargs):
                routing.set()
                release.wait(timeout=10)
                try:
                    return real_route(*args, **kwargs)
                finally:
                    routed.set()

            router.route = held_route
            loop = asyncio.get_running_loop()
            async with aiohttp.ClientSession() as http:
                ws, client_id = await _connect(http, server)
                sid = _session_of(server, client_id)
                await ws.send_json({"type": "message", "content": HOST_TURN})
                held = await loop.run_in_executor(None, routing.wait, 10)
                await ws.close()
                let_go = await _let_go(server, client_id)
                at_close = _sessions(tmp_path)[sid]
                with caplog.at_level(logging.WARNING):
                    release.set()
                    finished = await loop.run_in_executor(None, routed.wait, 10)
                return (sid, held, let_go, finished, at_close,
                        _sessions(tmp_path)[sid])

    sid, held, let_go, finished, at_close, after = asyncio.run(scenario())
    assert held and finished and not model_calls, (
        f"control: the turn was not held in routing and then routed "
        f"(held {held}, routed {finished}, model {model_calls})")
    assert let_go, "control: the server never let the closed connection go"
    assert at_close["ended_at"] is not None and at_close[
        "end_reason"] == "connection closed", (
        f"control: the close did not end the session: {at_close}")
    assert after == at_close, (
        "the turn routed after its connection closed was counted on the "
        f"session the close had ended: {at_close} became {after}")
    reported = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and sid in r.getMessage()]
    assert reported, (
        f"a turn arrived for the ended session {sid} and nothing reported it")


# ── Control: green at both ends ──────────────────────────────────────────────


def test_control_closing_one_connection_leaves_the_others_session_open(tmp_path):
    """Two tabs; one is closed. The other tab's session is still open, with its
    turn counted."""
    model_calls: list = []

    async def scenario():
        async with _a_server(tmp_path, model_calls) as (server, _memory):
            async with aiohttp.ClientSession() as http:
                closing_ws, closing_id = await _connect(http, server)
                staying_ws, staying_id = await _connect(http, server)
                staying = _session_of(server, staying_id)
                await _take_a_turn(closing_ws, HOST_TURN)
                await _take_a_turn(staying_ws, DISK_TURN)
                await closing_ws.close()
                let_go = await _let_go(server, closing_id)
                row = _sessions(tmp_path)[staying]
                await staying_ws.close()
                return let_go, row

    let_go, row = asyncio.run(scenario())
    assert not model_calls, f"control: a model was called: {model_calls}"
    assert let_go, "control: the server never let the closed connection go"
    assert row["ended_at"] is None and row["turn_count"] == 1, (
        f"closing one tab touched the other tab's session: {row}")

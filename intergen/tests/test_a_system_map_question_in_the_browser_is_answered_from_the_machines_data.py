# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A question about this machine's state, asked in the browser, is answered from the machine's data.

THE DEFECT. The router answers a question about the machine's live state ("is
everything ok?", "what's failing?") from the system map its state cache holds:
the model reads true data and is offered no tools. In the browser every turn is
routed with decide_only, and for this route the router returns only its
decision, with no text. The web server's stream path holds the branch that
builds that grounded prompt and streams the answer, but the message handler
streamed only the two model routes, so the system-map decision fell through to
the handler's fallback: the person got an empty reply, and the exchange was
written into the conversation as answered.

So, in the browser, a system-map question is streamed through the stream path:
the model is given the system-map data and no tools, its answer is delivered,
and the turn is counted once in the connection's own memory session.

WHAT THE CASES DRIVE. The shipped ``WebServer._handle_client_message`` over a
real ``ConversationRouter`` on the real tool registry (tool offering locked, as
the daemon locks it) and one ``MemoryManager`` on a throwaway store, the router
detached as the daemon leaves it. Stand-ins, each named: the state cache (it
holds a fixed system map, and refuses any other question it is asked, so an
unexpected read fails the case instead of passing quietly), the model's entry
points (a fixed answer; each call keeps the prompt and the tools it was given)
and the connection's socket (it keeps every frame sent). No socket, model
server or network is reached.
"""

from __future__ import annotations

import asyncio
import sqlite3

from intergen.memory import MemoryManager

_SYSTEM_MAP = "services: 0 failed; disk: 41% used; load average: 0.30"
_MODEL_ANSWER = "Everything looks healthy: no service has failed and the disk is 41% used."


class _RecordingSocket:
    """The connection's socket: keeps every frame the server sends."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)


class _StateCache:
    """The state cache: one fixed system map, or none. Any other read refuses."""

    def __init__(self, system_map):
        self._system_map = system_map
        self.reads: list[str] = []

    def lookup_for_query(self, query):
        self.reads.append("lookup_for_query")
        return None

    def get_system_map_data(self, query):
        self.reads.append("get_system_map_data")
        return self._system_map

    def __getattr__(self, name):
        raise AttributeError(f"the state cache stand-in was asked for {name}")


def _a_tab(tmp_path, system_map):
    """A browser tab on a server over a real router whose state cache holds
    ``system_map``. Returns the server, the tab, the cache and the model's
    calls."""
    from intergen.interfaces.types import LLMResponse
    from intergen.llm import LLMRouter
    from intergen.router import ConversationRouter
    from intergen.semantic import SemanticMatcher
    from intergen.session_manager import SessionManager
    from intergen.tool_registry import ToolRegistry
    from intergen.web_server import ConnectionContext, WebServer

    model_calls: list[dict] = []
    llm = LLMRouter(config=None)

    def _chat(messages, **kwargs):
        model_calls.append({"entry": "chat", "messages": list(messages)})
        return LLMResponse(text=_MODEL_ANSWER, model="stand-in")

    def _stream(messages, *args, **kwargs):
        model_calls.append({"entry": "stream", "messages": list(messages),
                            "tools": kwargs.get("tools")})
        yield _MODEL_ANSWER

    llm.chat, llm.stream, llm.stream_with_tools = _chat, _stream, _stream
    registry = ToolRegistry()
    registry.discover_tools()
    registry.set_tool_offering_locked(True)
    memory = MemoryManager(str(tmp_path / "memory.db"))
    cache = _StateCache(system_map)
    router = ConversationRouter(tool_registry=registry,
                                semantic_matcher=SemanticMatcher(embedder=None),
                                llm=llm, lock_dispatch=True, memory=memory,
                                state_cache=cache)
    router.detach_conversation()
    server = WebServer(router=router, llm=llm, tools=None, governance=None,
                       memory=memory)
    server._sessions = SessionManager(sessions_dir=tmp_path / "transcripts")
    tab = ConnectionContext(client_id="web_system_map", source_interface="web",
                            ws=_RecordingSocket(),
                            conversation=router.new_conversation())
    return server, tab, cache, model_calls


def _turns_counted(tmp_path, session_id) -> int:
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    try:
        return conn.execute("SELECT turn_count FROM sessions WHERE session_id = ?",
                            (session_id,)).fetchone()[0]
    finally:
        conn.close()


def test_a_system_map_question_in_the_browser_is_answered_from_the_machines_data(
        tmp_path):
    """ "is everything ok?" in the browser: the answer is streamed from the model
    given the system map and no tools, never delivered empty, and the turn is
    counted once in the tab's own session."""
    server, tab, cache, model_calls = _a_tab(tmp_path, _SYSTEM_MAP)

    asyncio.run(server._handle_client_message(
        tab, {"type": "message", "content": "is everything ok?"}))

    assert "get_system_map_data" in cache.reads, (
        f"control: the router never read the system map: {cache.reads}")
    empty = [m for m in tab.ws.sent
             if m.get("type") == "response" and not (m.get("content") or "").strip()]
    assert not empty, (
        "a question about this machine's state was answered with an empty "
        f"reply: {empty}")
    ends = [m for m in tab.ws.sent if m.get("type") == "stream_end"]
    assert len(ends) == 1 and ends[0].get("full_response") == _MODEL_ANSWER, (
        "the system-map answer was not streamed to the tab: "
        f"{[(m.get('type'), m.get('source')) for m in tab.ws.sent]}")
    streamed = [c for c in model_calls if c["entry"] == "stream"]
    assert len(streamed) == 1 and any(
        _SYSTEM_MAP in (getattr(m, "content", "") or "")
        for m in streamed[0]["messages"]), (
        "the model was not given the system map to answer from: "
        f"{len(streamed)} streamed call(s)")
    assert not streamed[0]["tools"], (
        f"the system-map answer was offered tools: {streamed[0]['tools']}")
    counted = _turns_counted(tmp_path, tab.conversation.memory_session_id)
    assert counted == 1, (
        f"the tab's session counted {counted} turns for the one it was served")


def test_control_a_question_the_system_map_cannot_ground_goes_on_to_the_models_turn(
        tmp_path):
    """Control, green at both ends: with no system map to read, the same
    question is the model's own turn, streamed, and the case sees its answer."""
    server, tab, cache, model_calls = _a_tab(tmp_path, None)

    asyncio.run(server._handle_client_message(
        tab, {"type": "message", "content": "is everything ok?"}))

    ends = [m for m in tab.ws.sent if m.get("type") == "stream_end"]
    assert len(ends) == 1 and ends[0].get("full_response") == _MODEL_ANSWER, (
        f"{[(m.get('type'), m.get('source')) for m in tab.ws.sent]}")
    assert not any(_SYSTEM_MAP in (getattr(m, "content", "") or "")
                   for c in model_calls for m in c["messages"])

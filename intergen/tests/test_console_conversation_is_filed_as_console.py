# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A console conversation is filed as a console session, and its own list shows it.

The server writes a conversation's record lazily, at its first turn, through
``SessionManager.save``, and a save for a record that does not exist yet
creates it. That create took its default interface, "web", whatever the
connection was, and none of the server's saves said otherwise. So every console
conversation was filed as a web session: the daemon logged "Session created:
... (web)" beside "WebSocket connected: console_... (source=console)", the
session list sent back to the console after its turn did not carry the
conversation, and the web page's list did. The record named a web session
nobody had.

Three doors reach a save that creates a record: the first turn's incremental
persist, /new leaving a conversation whose record was never written, and a
switch leaving one. Each runs on the real server object, its session store on a
temporary directory, with a recording stand-in for the socket. The control is a
web connection through the same doors, filed as web.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import web_server as ws_mod
from intergen.interfaces.types import Message, MessageRole
from intergen.session_manager import SessionManager
from intergen.web_server import ConnectionContext, WebServer

CONVERSATION_ID = "session_c0ffee01"
OTHER_ID = "session_c0ffee02"

FIRST_TURN = (
    ("user", "what is using my disk?"),
    ("assistant", "Your home directory holds most of it: 41 GB in ~/Videos."),
)


class _RecordingWS:
    """Stands in for the aiohttp WebSocketResponse; records every send."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload) -> None:
        self.sent.append(payload)

    def of_type(self, type_name: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == type_name]


def _server(sessions_dir: Path) -> WebServer:
    """The real server object with its session store on ``sessions_dir``.

    The store is replaced while the server is built, so not even the
    constructor touches the session directory under the home directory."""
    with mock.patch.object(ws_mod, "SessionManager",
                           lambda: SessionManager(sessions_dir=sessions_dir)):
        return WebServer(router=None, llm=None, tools=None, governance=None)


def _connection(interface: str) -> ConnectionContext:
    """A connection on ``interface`` that has had one turn and has no record."""
    ctx = ConnectionContext(client_id=f"{interface}_0123456789ab",
                            source_interface=interface, ws=_RecordingWS())
    ctx.current_session_id = CONVERSATION_ID
    ctx.session_history = [
        Message(role=MessageRole(role), content=text)
        for role, text in FIRST_TURN]
    return ctx


def _first_turn_persisted(server: WebServer, ctx: ConnectionContext) -> None:
    asyncio.run(server._persist_and_list(ctx))


def _new_conversation_started(server: WebServer, ctx: ConnectionContext) -> None:
    asyncio.run(server._handle_new_session(ctx, {}))


def _switched_away(server: WebServer, ctx: ConnectionContext) -> None:
    server._sessions.create(session_id=OTHER_ID,
                            source_interface=ctx.source_interface)
    asyncio.run(server._handle_switch_session(ctx, {"session_id": OTHER_ID}))


DOORS = (
    ("the first turn's persist", _first_turn_persisted),
    ("/new leaving the conversation", _new_conversation_started),
    ("a switch leaving the conversation", _switched_away),
)


def _filed(sessions_dir: Path) -> dict:
    with open(sessions_dir / f"{CONVERSATION_ID}.json") as f:
        return json.load(f)


def _listed_ids(ws: _RecordingWS) -> list[list[str]]:
    """The session ids each session_list frame sent to this connection held."""
    return [[s.get("session_id") for s in frame.get("sessions", [])]
            for frame in ws.of_type("session_list")]


class AConsoleConversationIsFiledAsConsole(unittest.TestCase):
    """intergen/session_manager.py save(), and the server's saves that call it."""

    def _through(self, door, interface: str):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sessions_dir = Path(tmp.name)
        server = _server(sessions_dir)
        ctx = _connection(interface)
        self.assertFalse((sessions_dir / f"{CONVERSATION_ID}.json").exists(),
                         "guard: the conversation must have no record yet")
        door(server, ctx)
        return server, ctx, sessions_dir

    def test_a_console_conversation_is_filed_as_console_at_every_door(self):
        for label, door in DOORS:
            with self.subTest(door=label):
                server, ctx, sessions_dir = self._through(door, "console")
                record = _filed(sessions_dir)
                self.assertEqual(
                    "console", record.get("source_interface"),
                    f"{label}: a console conversation was filed as "
                    f"{record.get('source_interface')!r}; the web page's list "
                    "shows it and the console's own does not")
                self.assertEqual(
                    [m["content"] for m in record["messages"]],
                    [text for _role, text in FIRST_TURN])
                console_ids = [s["session_id"] for s in
                               server._sessions.list_sessions("console")]
                web_ids = [s["session_id"] for s in
                           server._sessions.list_sessions("web")]
                self.assertIn(CONVERSATION_ID, console_ids)
                self.assertNotIn(CONVERSATION_ID, web_ids)
                listed = _listed_ids(ctx.ws)
                self.assertTrue(listed, f"{label}: no session_list was sent")
                self.assertIn(
                    CONVERSATION_ID, listed[-1],
                    f"{label}: the session list sent to the console after "
                    f"the save does not carry its conversation: {listed!r}")

    def test_control_a_web_conversation_is_filed_as_web(self):
        for label, door in DOORS:
            with self.subTest(door=label):
                server, ctx, sessions_dir = self._through(door, "web")
                self.assertEqual("web",
                                 _filed(sessions_dir).get("source_interface"))
                listed = _listed_ids(ctx.ws)
                self.assertTrue(listed)
                self.assertIn(CONVERSATION_ID, listed[-1])


if __name__ == "__main__":
    unittest.main()

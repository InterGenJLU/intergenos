# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Web UI: session-switch replay + the file/screenshot command registry.

Three defects, one surface, and each was invisible to a diff review because the
broken half looked correct on its own:

  * ``session_switched`` reported a ``message_count`` and shipped no messages.
    The server log said "history=2 msgs" while the pane rendered empty — a count
    and a view that disagree. These tests assert on what CROSSES THE WIRE, in
    order, with roles, so a payload that carries a count but no transcript fails
    here (the rendered-not-diffed spirit of
    installer/tests/test_gui_done_rendered_string.py).

  * ``/screenshot`` and ``/file`` were sent by the client and registered by
    nobody, so every press produced ``unknown_command``. The registry test walks
    the real handler and asserts the error is gone AND that the tool route is
    the ordinary gated one — a private capture path would pass a "no error"
    check while bypassing consent.

  * ``/file`` with a path must go through the registered ``read_file`` tool
    rather than opening the file in the handler: a file body is ingress, and
    reading it directly would step around the gate that exists for exactly that.

No live model, no browser, no display: the WebSocket is a recording double.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from intergen.interfaces.types import Message, MessageRole, ToolResult
from intergen.interfaces.provenance import Provenance
from intergen import web_server as ws_mod
from intergen.web_server import WebServer, ConnectionContext, _history_wire_format


class _RecordingWS:
    """Stands in for the aiohttp WebSocketResponse; records every send."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)

    def of_type(self, type_name):
        return [m for m in self.sent if m.get("type") == type_name]

    def first(self, type_name):
        found = self.of_type(type_name)
        return found[0] if found else None


def _ctx(ws=None):
    return ConnectionContext(
        client_id="test-client",
        source_interface="web",
        ws=ws or _RecordingWS(),
    )


def _server(**kw):
    """A WebServer with everything optional left absent unless a test sets it."""
    defaults = dict(router=None, llm=None, tools=None, governance=None)
    defaults.update(kw)
    return WebServer(**defaults)


# ── The wire format ────────────────────────────────────────────────────────
class HistoryWireFormatTests(unittest.TestCase):
    def test_roles_and_order_survive(self):
        history = [
            Message(role=MessageRole.USER, content="first"),
            Message(role=MessageRole.ASSISTANT, content="second"),
            Message(role=MessageRole.USER, content="third"),
        ]
        wire = _history_wire_format(history)
        self.assertEqual([m["content"] for m in wire],
                         ["first", "second", "third"],
                         "transcript order is the transcript's meaning")
        self.assertEqual([m["role"] for m in wire],
                         ["user", "assistant", "user"])

    def test_empty_history_is_empty_list_not_none(self):
        self.assertEqual(_history_wire_format([]), [])

    def test_every_entry_carries_content_key(self):
        wire = _history_wire_format([Message(role=MessageRole.USER, content="")])
        self.assertIn("content", wire[0])
        self.assertIn("role", wire[0])


# ── The defect these tests exist for ───────────────────────────────────────
class SessionSwitchShipsTranscriptTests(unittest.TestCase):
    """A switch must deliver what it loaded, not merely count it."""

    def _switch(self, loaded_messages):
        srv = _server()
        sessions = mock.MagicMock()
        sessions.load.return_value = {"messages": loaded_messages}
        srv._sessions = sessions
        srv._send_session_list = mock.AsyncMock()
        ctx = _ctx()
        with mock.patch.object(ws_mod.SessionManager,
                               "_validate_session_id", return_value=None):
            asyncio.run(srv._handle_switch_session(
                ctx, {"session_id": "session_abc12345"}))
        return ctx.ws.first("session_switched")

    def test_switch_carries_the_loaded_messages(self):
        reply = self._switch([
            Message(role=MessageRole.USER, content="why is the sky blue"),
            Message(role=MessageRole.ASSISTANT, content="rayleigh scattering"),
        ])
        self.assertIsNotNone(reply, "no session_switched was sent at all")
        self.assertIn("messages", reply,
                      "the switch shipped a count with no transcript — the "
                      "exact defect: the pane renders empty")
        self.assertEqual(len(reply["messages"]), 2)
        self.assertEqual(reply["messages"][0]["content"], "why is the sky blue")
        self.assertEqual(reply["messages"][1]["role"], "assistant")

    def test_count_and_payload_cannot_disagree(self):
        reply = self._switch([
            Message(role=MessageRole.USER, content="a"),
            Message(role=MessageRole.ASSISTANT, content="b"),
            Message(role=MessageRole.USER, content="c"),
        ])
        self.assertEqual(reply["message_count"], len(reply["messages"]),
                         "message_count and the shipped transcript came from "
                         "different places")

    def test_empty_session_ships_an_empty_list(self):
        reply = self._switch([])
        self.assertEqual(reply["message_count"], 0)
        self.assertEqual(reply["messages"], [])

    def test_round_trip_a_to_b_to_a_loses_nothing(self):
        """A→B→A: the messages A reported the first time come back the second."""
        srv = _server()
        store = {
            "session_aaaaaaaa": {"messages": [
                Message(role=MessageRole.USER, content="alpha-one"),
                Message(role=MessageRole.ASSISTANT, content="alpha-two"),
            ]},
            "session_bbbbbbbb": {"messages": [
                Message(role=MessageRole.USER, content="beta-one"),
            ]},
        }
        sessions = mock.MagicMock()
        sessions.load.side_effect = lambda sid: store.get(sid)
        sessions.save.side_effect = lambda sid, hist, **kw: store.__setitem__(
            sid, {"messages": list(hist)})
        srv._sessions = sessions
        srv._send_session_list = mock.AsyncMock()
        ctx = _ctx()

        with mock.patch.object(ws_mod.SessionManager,
                               "_validate_session_id", return_value=None):
            asyncio.run(srv._handle_switch_session(
                ctx, {"session_id": "session_aaaaaaaa"}))
            first = ctx.ws.of_type("session_switched")[-1]
            asyncio.run(srv._handle_switch_session(
                ctx, {"session_id": "session_bbbbbbbb"}))
            asyncio.run(srv._handle_switch_session(
                ctx, {"session_id": "session_aaaaaaaa"}))
            third = ctx.ws.of_type("session_switched")[-1]

        self.assertEqual([m["content"] for m in first["messages"]],
                         [m["content"] for m in third["messages"]],
                         "a round trip through another session lost content")


# ── The command registry ───────────────────────────────────────────────────
class SlashCommandRegistryTests(unittest.TestCase):
    """/screenshot and /file must reach a handler, not unknown_command."""

    @staticmethod
    def _run_command_and_drain(srv, ctx, data):
        """Dispatch a slash command and await anything it spawned.

        The gating commands run off the receive loop on purpose, so a test that
        just calls the handler would race the task it created.
        """
        async def scenario():
            await srv._handle_slash_command(ctx, data)
            for t in list(ctx.tool_tasks):
                await t
        asyncio.run(scenario())

    def test_screenshot_is_registered(self):
        srv = _server()
        srv._run_user_invoked_tool = mock.AsyncMock()
        ctx = _ctx()
        self._run_command_and_drain(srv, ctx, {"command": "/screenshot"})
        self.assertEqual(ctx.ws.of_type("error"), [],
                         "/screenshot still errors — this is the reported "
                         "unknown-command popup")
        srv._run_user_invoked_tool.assert_awaited_once()
        self.assertEqual(srv._run_user_invoked_tool.await_args.args[1],
                         "take_screenshot")

    def test_file_is_registered(self):
        srv = _server()
        srv._run_user_invoked_tool = mock.AsyncMock()
        ctx = _ctx()
        self._run_command_and_drain(
            srv, ctx, {"command": "/file", "path": "/etc/hostname"})
        self.assertEqual(ctx.ws.of_type("error"), [])
        srv._run_user_invoked_tool.assert_awaited_once()

    def test_unknown_commands_still_error(self):
        """Registering two commands must not make the registry permissive."""
        srv = _server()
        ctx = _ctx()
        asyncio.run(srv._handle_slash_command(ctx, {"command": "/nonsense"}))
        err = ctx.ws.first("error")
        self.assertIsNotNone(err)
        self.assertEqual(err["code"], "unknown_command")

    def test_case_insensitive(self):
        srv = _server()
        srv._run_user_invoked_tool = mock.AsyncMock()
        ctx = _ctx()
        self._run_command_and_drain(srv, ctx, {"command": "/SCREENSHOT"})
        self.assertEqual(ctx.ws.of_type("error"), [])


class FileCommandRoutingTests(unittest.TestCase):
    """Chosen-content and named-path are different trust cases, routed apart."""

    def test_chosen_content_reaches_the_model_context(self):
        srv = _server()
        ctx = _ctx()
        asyncio.run(srv._handle_file_command(
            ctx, {"filename": "notes.txt", "content": "the file body"}))
        self.assertTrue(
            any("the file body" in m.content for m in ctx.session_history),
            "the chosen file's content never reached the conversation")
        loaded = ctx.ws.first("file_loaded")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["filename"], "notes.txt")

    def test_chosen_content_does_not_read_the_filesystem(self):
        """The bytes came from the browser; nothing on this box is opened."""
        srv = _server()
        ctx = _ctx()
        with mock.patch("builtins.open",
                        side_effect=AssertionError("handler opened a file")):
            asyncio.run(srv._handle_file_command(
                ctx, {"filename": "a.txt", "content": "body"}))

    def test_named_path_routes_through_the_gated_read_file_tool(self):
        srv = _server()
        srv._run_user_invoked_tool = mock.AsyncMock()
        ctx = _ctx()

        async def scenario():
            await srv._handle_file_command(ctx, {"path": "/etc/hostname"})
            for t in list(ctx.tool_tasks):
                await t
        asyncio.run(scenario())
        srv._run_user_invoked_tool.assert_awaited_once()
        args = srv._run_user_invoked_tool.await_args.args
        self.assertEqual(args[1], "read_file",
                         "a named path must go through the registered ingress "
                         "tool, never a direct open in the handler")
        self.assertEqual(args[2], {"path": "/etc/hostname"})

    def test_neither_content_nor_path_is_an_honest_error(self):
        srv = _server()
        ctx = _ctx()
        asyncio.run(srv._handle_file_command(ctx, {}))
        err = ctx.ws.first("error")
        self.assertIsNotNone(err)
        self.assertEqual(err["code"], "missing_field")


class GatedCommandRunsOffTheReceiveLoopTests(unittest.TestCase):
    """The F2 deny-hang discipline, applied to user-invoked tools.

    Caught by the live proof, not by a unit test: with the gate mocked, awaiting
    the tool inline in the slash handler looks perfectly correct. On a real
    socket it deadlocks — the handler runs ON the receive loop, so while it
    waits for the consent decision the loop cannot read the very gate_decision
    that would release it. The card renders, the user clicks Allow, and the
    click is never read.

    So the assertion is structural: a slash command that can gate must RETURN
    promptly, having spawned the work, and must never block on the tool.
    """

    def test_screenshot_handler_returns_without_awaiting_the_tool(self):
        srv = _server()
        started = asyncio.Event()
        release = asyncio.Event()

        async def _never_finishes(ctx, name, args):
            started.set()
            await release.wait()          # stands in for an unanswered gate

        srv._run_user_invoked_tool = _never_finishes

        async def scenario():
            ctx = _ctx()
            # The handler must complete even though the tool never does.
            await asyncio.wait_for(
                srv._handle_slash_command(ctx, {"command": "/screenshot"}),
                timeout=2.0,
            )
            await asyncio.wait_for(started.wait(), timeout=2.0)
            release.set()
            for t in list(ctx.tool_tasks):
                await t

        asyncio.run(scenario())

    def test_file_path_handler_returns_without_awaiting_the_tool(self):
        srv = _server()
        release = asyncio.Event()

        async def _never_finishes(ctx, name, args):
            await release.wait()

        srv._run_user_invoked_tool = _never_finishes

        async def scenario():
            ctx = _ctx()
            await asyncio.wait_for(
                srv._handle_slash_command(
                    ctx, {"command": "/file", "path": "/etc/hostname"}),
                timeout=2.0,
            )
            release.set()
            for t in list(ctx.tool_tasks):
                await t

        asyncio.run(scenario())

    def test_the_spawned_task_is_held_so_it_is_not_collected(self):
        """A detached task with no reference can be garbage-collected mid-gate."""
        srv = _server()
        release = asyncio.Event()

        async def _pending(ctx, name, args):
            await release.wait()

        srv._run_user_invoked_tool = _pending

        async def scenario():
            ctx = _ctx()
            await srv._handle_slash_command(ctx, {"command": "/screenshot"})
            self.assertEqual(len(ctx.tool_tasks), 1,
                             "the in-flight tool task is not referenced")
            release.set()
            for t in list(ctx.tool_tasks):
                await t
            await asyncio.sleep(0)
            self.assertEqual(len(ctx.tool_tasks), 0,
                             "finished tasks must be discarded, not accumulated")

        asyncio.run(scenario())


class UserInvokedToolGateTests(unittest.TestCase):
    """A user-invoked tool is gated like any other, and reports honestly."""

    def _server_with_tool(self, result=None, tool_present=True):
        srv = _server()
        tools = mock.MagicMock()
        tools.get_tool.return_value = object() if tool_present else None
        tools.execute.return_value = result or ToolResult(
            call_id="c1", name="take_screenshot", success=True,
            content="captured bytes")
        srv._tools = tools
        return srv, tools

    def test_gate_runs_before_execution(self):
        srv, tools = self._server_with_tool()
        srv._evaluate_tool_with_gate = mock.AsyncMock(return_value="approved")
        ctx = _ctx()
        asyncio.run(srv._run_user_invoked_tool(ctx, "take_screenshot", {}))
        srv._evaluate_tool_with_gate.assert_awaited_once()
        tools.execute.assert_called_once()

    def test_provenance_is_user_direct(self):
        srv, tools = self._server_with_tool()
        srv._evaluate_tool_with_gate = mock.AsyncMock(return_value="approved")
        ctx = _ctx()
        asyncio.run(srv._run_user_invoked_tool(ctx, "take_screenshot", {}))
        call = srv._evaluate_tool_with_gate.await_args.args[2]
        self.assertEqual(call.source_of_request, Provenance.USER_DIRECT)
        self.assertEqual(call.name, "take_screenshot")

    def test_denial_does_not_execute_and_says_so(self):
        srv, tools = self._server_with_tool()
        srv._evaluate_tool_with_gate = mock.AsyncMock(return_value="denied")
        ctx = _ctx()
        asyncio.run(srv._run_user_invoked_tool(ctx, "take_screenshot", {}))
        tools.execute.assert_not_called()
        executed = ctx.ws.first("tool_executed")
        self.assertIsNotNone(executed, "a denial reported nothing at all")
        self.assertFalse(executed["success"])

    def test_success_reports_and_joins_the_conversation(self):
        srv, tools = self._server_with_tool()
        srv._evaluate_tool_with_gate = mock.AsyncMock(return_value="approved")
        ctx = _ctx()
        asyncio.run(srv._run_user_invoked_tool(ctx, "take_screenshot", {}))
        executed = ctx.ws.first("tool_executed")
        self.assertTrue(executed["success"])
        self.assertTrue(
            any("captured bytes" in m.content for m in ctx.session_history),
            "the tool output never reached the context the model reads")

    def test_unregistered_tool_is_an_honest_error_not_a_crash(self):
        srv, tools = self._server_with_tool(tool_present=False)
        ctx = _ctx()
        asyncio.run(srv._run_user_invoked_tool(ctx, "take_screenshot", {}))
        err = ctx.ws.first("error")
        self.assertIsNotNone(err)
        self.assertEqual(err["code"], "tool_unavailable")

    def test_tool_failure_is_reported_not_swallowed(self):
        srv, tools = self._server_with_tool()
        srv._evaluate_tool_with_gate = mock.AsyncMock(return_value="approved")
        tools.execute.side_effect = RuntimeError("no compositor surface")
        ctx = _ctx()
        asyncio.run(srv._run_user_invoked_tool(ctx, "take_screenshot", {}))
        executed = ctx.ws.first("tool_executed")
        self.assertIsNotNone(executed)
        self.assertFalse(executed["success"])
        self.assertIn("no compositor surface", executed["summary"])


if __name__ == "__main__":
    unittest.main()

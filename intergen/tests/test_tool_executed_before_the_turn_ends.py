# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The record that a tool ran must arrive before the turn ends — and the
harness that grades turns must keep reading long enough to see it.

THE DEFECT, measured against a running daemon on 2026-09-18. A turn that really
ran a tool was recorded as:

    session_list, turn_ack, stream_start, tool_ack, stream_token, stream_end

with NO tool_executed card, while the daemon's own log named the tool it had
run (read_file, /etc/os-release, 318 bytes). The browser server sent the cards
AFTER stream_end, and stream_end ends the turn.

TWO THINGS WERE WRONG AND EACH HID THE OTHER.

  * THE SERVER put the user's record of an action after the turn's terminal
    frame. A card that arrives after the end is a card a consumer may never
    render, and the card is the only place the panel says what was done.
  * THE HARNESS stopped reading at the terminal frame, so the browser battery
    could not observe a tool_executed on a streamed turn AT ALL. Its
    action-frame grading — the thing that decides whether a "do not use tools"
    question was honoured — rested on two frames where it claims three. An
    instrument that cannot observe one of the things it grades certifies its
    absence without ever having looked.

Fixing only the server would leave the instrument blind to the next frame sent
late; fixing only the harness would leave the ordering wrong for every other
client. Both are here, and both are tested here.
"""

from __future__ import annotations

import asyncio
import json
import unittest

import aiohttp

from intergen.interfaces.types import Message, MessageRole, RouteResult, ToolCall
from intergen.router import ConversationRouter
from intergen.tests import ws_harness
from intergen.web_server import ConnectionContext, WebServer


# ── the server half ────────────────────────────────────────────────────────
class _RecordingWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)

    def types(self):
        return [m.get("type") for m in self.sent]


class _ChatReply:
    def __init__(self, text):
        self.text = text


class _FakeLLM:
    _endpoint = "http://127.0.0.1:8080/v1/chat/completions"

    def __init__(self, items):
        self._items = list(items)

    def build_system_messages(self, query_type="general", with_tools=True):
        return [Message(role=MessageRole.SYSTEM, content="system prompt")]

    def stream_with_tools(self, messages, tools=None, image_data=None,
                          max_tokens=None):
        yield from self._items

    def chat(self, messages, max_tokens=None):
        return _ChatReply("ok")

    def continue_after_tool_call(self, messages, tool_call, tool_result, *,
                                 success=True, executed=True, max_tokens=400,
                                 temperature=0.3):
        return _ChatReply(f"({tool_call.name} came back)")


def _detached_router(llm):
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = None
    r._llm = llm
    r._last_semantic_score = None
    r.detach_conversation()
    return r


def _a_tool_call(name="read_file", call_id="call1"):
    from intergen.interfaces.provenance import Provenance
    return ToolCall(name=name, arguments={}, call_id=call_id,
                    source_of_request=Provenance.USER_IMPLIED)


def _run_turn(items, user_msg="read /etc/os-release"):
    llm = _FakeLLM(items)
    router = _detached_router(llm)
    server = WebServer(router=router, llm=llm, tools=None, governance=None)
    ctx = ConnectionContext(client_id="c1", source_interface="web",
                            ws=_RecordingWS(),
                            conversation=router.new_conversation())
    asyncio.run(server._stream_llm_response(
        ctx, "turn1", user_msg,
        RouteResult(text="", source="llm_tools", handled=False)))
    return ctx


class TheCardArrivesBeforeTheTurnEndsTest(unittest.TestCase):

    def test_tool_executed_precedes_stream_end(self):
        """THE DEFECT. The card is the user's record that an action happened."""
        ctx = _run_turn([_a_tool_call()])
        types = ctx.ws.types()
        self.assertIn("tool_executed", types, types)
        self.assertIn("stream_end", types, types)
        self.assertLess(types.index("tool_executed"), types.index("stream_end"),
                        types)

    def test_stream_end_is_still_the_last_frame(self):
        ctx = _run_turn([_a_tool_call()])
        self.assertEqual(ctx.ws.types()[-1], "stream_end", ctx.ws.types())

    def test_every_card_precedes_it(self):
        """However many cards a turn produced, all of them come first.

        This fixture has no tool registry, so the first call cannot run and the
        turn ends on the honest handoff — one card, not two. That is the
        fixture's shape, not a defect, and the ordering claim is about ALL the
        cards there are, whatever their number.
        """
        ctx = _run_turn([_a_tool_call("read_file", "call1"),
                         _a_tool_call("get_system_status", "call2")])
        types = ctx.ws.types()
        cards = [i for i, t in enumerate(types) if t == "tool_executed"]
        self.assertTrue(cards, types)
        self.assertTrue(all(i < types.index("stream_end") for i in cards),
                        types)

    def test_a_turn_with_no_tool_sends_no_card(self):
        ctx = _run_turn(["391"], user_msg="what is 17 times 23")
        self.assertNotIn("tool_executed", ctx.ws.types())
        self.assertEqual(ctx.ws.types()[-1], "stream_end")

    def test_the_card_still_carries_what_it_carried(self):
        """Moving the frame must not change its contents."""
        ctx = _run_turn([_a_tool_call()])
        card = [m for m in ctx.ws.sent if m.get("type") == "tool_executed"][0]
        self.assertEqual(card["tool_name"], "read_file")
        self.assertIn("success", card)
        self.assertIn("summary", card)


# ── the harness half ───────────────────────────────────────────────────────
class _Msg:
    def __init__(self, type_, data=None):
        self.type = type_
        self.data = data


class _ScriptedWS:
    """A websocket that hands out a fixed script of frames, then blocks.

    Blocking after the script is the point: a harness that keeps reading with
    no bound would hang here, and the settle window is what stops it.
    """

    def __init__(self, frames, then="block"):
        self._frames = list(frames)
        self._then = then
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive(self):
        if self._frames:
            return self._frames.pop(0)
        if self._then == "close":
            return _Msg(aiohttp.WSMsgType.CLOSED)
        await asyncio.Event().wait()   # never returns

    async def close(self):
        pass


def _text(payload):
    return _Msg(aiohttp.WSMsgType.TEXT, json.dumps(payload))


def _drive(frames, then="block", settle_s=0.2, deadline_s=5.0):
    r = ws_harness.WSTurnResult(query="q")
    asyncio.run(ws_harness._drive_turn(
        _ScriptedWS(frames, then), r, gate_decision=None,
        gate_action="respond", deadline_s=deadline_s, settle_s=settle_s))
    return r


class TheHarnessKeepsReadingAfterTheTerminalFrameTest(unittest.TestCase):

    def test_a_frame_sent_after_stream_end_is_recorded(self):
        """THE BLIND SPOT. Before this, the card below was never seen."""
        r = _drive([
            _text({"type": "stream_start", "turn_id": "t"}),
            _text({"type": "stream_token", "token": "hello"}),
            _text({"type": "stream_end", "full_response": "hello"}),
            _text({"type": "tool_executed", "tool_name": "read_file",
                   "success": True, "summary": "318 bytes"}),
        ])
        self.assertTrue(r.terminal)
        self.assertEqual(r.text, "hello")
        self.assertIn("tool_executed", [t for _at, t in r.events])
        self.assertEqual(r.late_frames, 1)

    def test_the_turn_is_still_terminal_at_the_terminal_frame(self):
        """Reading on must not move WHEN the turn ended."""
        r = _drive([
            _text({"type": "stream_end", "full_response": "hi"}),
            _text({"type": "tool_executed", "tool_name": "x", "success": True,
                   "summary": ""}),
        ])
        self.assertTrue(r.terminal)
        self.assertIsNotNone(r.terminal_at)
        self.assertEqual(r.closed_by, "client")

    def test_nothing_late_is_not_a_failure(self):
        r = _drive([
            _text({"type": "stream_end", "full_response": "hi"}),
        ])
        self.assertTrue(r.terminal)
        self.assertEqual(r.late_frames, 0)

    def test_a_socket_that_closes_ends_the_drain_at_once(self):
        r = _drive([_text({"type": "stream_end", "full_response": "hi"})],
                   then="close", settle_s=5.0, deadline_s=20.0)
        self.assertTrue(r.terminal)
        self.assertLess(r.elapsed_s, 2.0)

    def test_the_drain_is_bounded_and_does_not_hang(self):
        """The scripted socket blocks forever after the script; the window is
        the only thing that ends the turn."""
        r = _drive([_text({"type": "stream_end", "full_response": "hi"})],
                   settle_s=0.2, deadline_s=30.0)
        self.assertTrue(r.terminal)
        self.assertLess(r.elapsed_s, 3.0)

    def test_a_fast_path_response_is_terminal_as_before(self):
        """The non-streaming reply keeps its own break; it is not drained,
        because it is the whole turn and nothing follows it."""
        r = _drive([_text({"type": "response", "content": "Paris"})])
        self.assertTrue(r.terminal)
        self.assertEqual(r.text, "Paris")


class TheBatteryCanNowSeeTheCardTest(unittest.TestCase):
    """The battery already grades on tool_executed; it simply could not observe
    one. Nothing in the battery changes, and that is the point."""

    def test_the_action_frame_set_already_names_it(self):
        from intergen.tests import web_battery
        self.assertIn("tool_executed", web_battery.ACTION_FRAMES)
        self.assertIn("tool_ack", web_battery.ACTION_FRAMES)
        self.assertIn("gate_prompt", web_battery.ACTION_FRAMES)


if __name__ == "__main__":
    unittest.main()

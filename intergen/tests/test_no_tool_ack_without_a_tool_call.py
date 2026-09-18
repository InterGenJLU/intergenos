# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A turn that executes no tool must not tell the user a tool is running.

THE DEFECT, measured against the running assistant on 2026-09-18. The browser
server sent its hop-1 acknowledgement — the ``tool_ack`` frame, which the web
panel and the terminal client both render as a line like "Let me check on that
for you." — the instant the router's route was ``llm_tools``. That route means
tools were OFFERED to the model, not that the model called one. On the shipped
tier most tool-route turns are answered straight out of the model with no tool
at all, so the line was a standing promise of an action that never happened:
every one of the fourteen questions in the web battery that routed ``llm_tools``
emitted it, and not one of them executed a tool.

The clearest case is the battery's no-action question, verbatim:

    Calculate 17 times 23 mentally. Reply with the number only. Do not use
    tools, run commands, access files, or contact external services.

The assistant answered "391", correctly and with no tool — under a line
promising the person an action they had just forbidden. The battery graded it a
failure on the frame.

WHAT THE FIX HAS TO DO. The frame is a claim, so it is made where it is true:
at the FIRST REAL TOOL CALL in the stream, once per turn. It still asserts
nothing about the outcome, so it composes with success, a consent prompt and a
refusal alike — a turn that asks for an action and is refused did ask for it.
What it may no longer do is appear on a turn that resolves without one.

These tests drive the real ``_stream_llm_response`` and the real
``_process_llm_stream`` with only the model client faked, in the idiom of
test_web_model_turn_on_detached_router.py beside them. No daemon is started and
no tool is run.
"""

from __future__ import annotations

import asyncio
import threading
import unittest

from intergen.interfaces.types import Message, MessageRole, RouteResult, ToolCall
from intergen.router import ConversationRouter
from intergen.web_server import ConnectionContext, WebServer


# The battery's no-action question, verbatim.
ROW22_SENTENCE = (
    "Calculate 17 times 23 mentally. Reply with the number only. Do not use "
    "tools, run commands, access files, or contact external services."
)


class _RecordingWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    def of_type(self, type_name):
        return [m for m in self.sent if m.get("type") == type_name]

    def types(self):
        return [m.get("type") for m in self.sent]


class _ChatReply:
    def __init__(self, text: str):
        self.text = text


class _FakeLLM:
    """Streams whatever items it is given — text tokens, ToolCall objects, or
    both — so a turn with and without a tool call are the same code path with
    different model output, which is exactly the difference under test."""
    _endpoint = "http://127.0.0.1:8080/v1/chat/completions"

    def __init__(self, items, chat_text="ok"):
        self._items = list(items)
        self._chat_text = chat_text
        self.tools_offered: list[list] = []

    def build_system_messages(self, query_type="general", with_tools=True):
        return [Message(role=MessageRole.SYSTEM, content="system prompt")]

    def stream_with_tools(self, messages, tools=None, image_data=None,
                          max_tokens=None):
        self.tools_offered.append(list(tools or []))
        yield from self._items

    def chat(self, messages, max_tokens=None):
        return _ChatReply(self._chat_text)

    def continue_after_tool_call(self, messages, tool_call, tool_result,
                                 *, success=True, executed=True,
                                 max_tokens=400, temperature=0.3):
        """The synthesis hop the streamed path runs after a tool returns."""
        return _ChatReply(f"({tool_call.name} came back)")


def _detached_router(llm) -> ConversationRouter:
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


def _server_and_ctx(llm, router):
    server = WebServer(router=router, llm=llm, tools=None, governance=None)
    ctx = ConnectionContext(client_id="c1", source_interface="web",
                            ws=_RecordingWS(),
                            conversation=router.new_conversation())
    return server, ctx


def _run_turn(items, user_msg, source="llm_tools"):
    llm = _FakeLLM(items)
    router = _detached_router(llm)
    server, ctx = _server_and_ctx(llm, router)
    route_result = RouteResult(text="", source=source, handled=False)
    asyncio.run(server._stream_llm_response(ctx, "turn1", user_msg,
                                            route_result))
    return server, ctx


def _a_tool_call(name="get_system_status", call_id="call1"):
    """A tool call as the model emits one. The provenance label is required by
    the dispatcher on every call, so it is set here too rather than defaulted."""
    from intergen.interfaces.provenance import Provenance
    return ToolCall(name=name, arguments={}, call_id=call_id,
                    source_of_request=Provenance.USER_IMPLIED)


class NoToolCallMeansNoAckTest(unittest.TestCase):

    def test_a_tool_route_answered_without_a_tool_sends_no_ack(self):
        """THE DEFECT. Tools were offered; the model answered from itself."""
        _server, ctx = _run_turn(["1991."], "What year was Linux released?")
        self.assertEqual(ctx.ws.of_type("tool_ack"), [], ctx.ws.types())

    def test_the_no_action_question_carries_no_action_frame_at_all(self):
        """The battery's own question, verbatim, and its own frame set."""
        _server, ctx = _run_turn(["391"], ROW22_SENTENCE)
        action_frames = [t for t in ctx.ws.types()
                         if t in ("gate_prompt", "tool_ack", "tool_executed")]
        self.assertEqual(action_frames, [], ctx.ws.types())

    def test_the_answer_itself_is_unchanged(self):
        """Removing the frame must not touch what the person is told."""
        _server, ctx = _run_turn(["391"], ROW22_SENTENCE)
        ends = ctx.ws.of_type("stream_end")
        self.assertEqual(len(ends), 1, ctx.ws.sent)
        self.assertEqual(ends[0]["full_response"], "391")

    def test_the_turn_still_streams_normally(self):
        """The frames around the removed one are untouched and in order."""
        _server, ctx = _run_turn(["3", "9", "1"], ROW22_SENTENCE)
        types = ctx.ws.types()
        self.assertIn("stream_start", types)
        self.assertEqual(types.count("stream_token"), 3)
        self.assertIn("stream_end", types)
        self.assertLess(types.index("stream_start"),
                        types.index("stream_token"))

    def test_a_conversational_route_is_unchanged(self):
        """llm_freeform never had the ack and still does not."""
        _server, ctx = _run_turn(["Hi there."], "hi", source="llm_freeform")
        self.assertEqual(ctx.ws.of_type("tool_ack"), [], ctx.ws.types())


class ARealToolCallStillAcksTest(unittest.TestCase):
    """The perceived-latency line is not removed — it is moved to the truth."""

    def test_a_tool_call_sends_exactly_one_ack(self):
        _server, ctx = _run_turn([_a_tool_call()], "How much disk is free?")
        acks = ctx.ws.of_type("tool_ack")
        self.assertEqual(len(acks), 1, ctx.ws.types())
        self.assertTrue(acks[0]["text"].strip())
        self.assertEqual(acks[0]["turn_id"], "turn1")

    def test_the_ack_arrives_after_the_stream_has_started(self):
        """The client shows it in the thinking indicator, which stream_start
        puts on screen; an ack before it would have nowhere to land."""
        _server, ctx = _run_turn([_a_tool_call()], "How much disk is free?")
        types = ctx.ws.types()
        self.assertLess(types.index("stream_start"), types.index("tool_ack"))

    def test_two_tool_calls_still_send_one_ack(self):
        """It is a hop-1 greeting for the turn, not a per-call frame; the
        per-call reassurance is the hop-2 tool_progress line."""
        _server, ctx = _run_turn(
            [_a_tool_call("get_system_status", "call1"),
             _a_tool_call("list_services", "call2")],
            "Check the system and the services.")
        self.assertEqual(len(ctx.ws.of_type("tool_ack")), 1, ctx.ws.types())

    def test_text_before_a_tool_call_does_not_suppress_the_ack(self):
        """A model that narrates and then calls a tool did take the action."""
        _server, ctx = _run_turn(["Let me look. ", _a_tool_call()],
                                 "How much disk is free?")
        self.assertEqual(len(ctx.ws.of_type("tool_ack")), 1, ctx.ws.types())

    def test_the_ack_still_composes_with_a_refused_call(self):
        """With no tool registry the call cannot run and the turn ends in the
        honest handoff. The turn DID ask for an action, so the ack stands."""
        _server, ctx = _run_turn([_a_tool_call()], "How much disk is free?")
        self.assertEqual(len(ctx.ws.of_type("tool_ack")), 1)
        ends = ctx.ws.of_type("stream_end")
        self.assertEqual(len(ends), 1, ctx.ws.sent)


class TheRouteIsNotTheClaimTest(unittest.TestCase):
    """The route still decides whether tools are OFFERED — that is unchanged
    and is a different question from whether one was used."""

    def test_the_tool_route_still_offers_tools_to_the_model(self):
        llm = _FakeLLM(["391"])
        router = _detached_router(llm)
        server = WebServer(router=router, llm=llm,
                           tools=_StubToolRegistry(), governance=None)
        ctx = ConnectionContext(client_id="c1", source_interface="web",
                                ws=_RecordingWS(),
                                conversation=router.new_conversation())
        asyncio.run(server._stream_llm_response(
            ctx, "turn1", ROW22_SENTENCE,
            RouteResult(text="", source="llm_tools", handled=False)))
        self.assertEqual(llm.tools_offered, [[{"name": "stub"}]])
        self.assertEqual(ctx.ws.of_type("tool_ack"), [])


class _StubToolRegistry:
    """Just enough registry to answer get_tool_schemas()."""

    def get_tool_schemas(self):
        return [{"name": "stub"}]

    def get_tool(self, name):
        return None


if __name__ == "__main__":
    unittest.main()

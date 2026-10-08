# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A grounded system-state question asked in the browser delivers bytes.

The web message handler decides the turn with ``route(decide_only=True)`` and
then dispatches on the verdict's ``source``. Three dispositions exist: the
fast-path allowlist (cache / keyword / semantic / decomposed / identity /
memory), which carries its own final text; the streamed pair, which the handler
hands to ``_stream_llm_response`` to generate; and a fallback whose own comment
promises it "delivers real bytes to the user".

``system_map`` — the grounded system-state route, the one that answers "is
everything ok", "what's running", "why is it slow", "recent errors" from TRUE
cached data — belongs to the streamed group and to nothing else. Under
``decide_only`` the router returns it as a decision with NO text
(``RouteResult(text="", source="system_map", handled=False)``), exactly as it
returns ``llm_tools`` and ``llm_freeform``, because the synthesis is meant to
happen once, in the streaming caller; ``decide_only`` is passed by the browser
server and by nothing else, so that branch exists for this one caller.
``_stream_llm_response`` carries the matching half: a dedicated ``system_map``
branch that builds the grounded, tool-free prompt from the cached data.

What is pinned here is that the two halves meet. A ``system_map`` verdict must
reach the streaming disposition: the turn streams a grounded answer, ends on a
terminal frame, and records what it delivered. It must NOT be handed to the
fallback, because the fallback sends the verdict's own text — and a streamed
verdict's text is the empty string. An empty ``response`` frame is not a
delivery: the page clears the thinking indicator and renders nothing (app.js
``handleResponse`` only draws when ``msg.content`` is truthy), no terminal frame
arrives, and the conversation — the model's next prompt, the transcript pane and
the saved session file, which are one list — gains an assistant message with no
content in it.

Driven through the real message handler with a real router whose ``route()``
returns the fixed ``system_map`` verdict, a cached-data double for the state
cache, and only the model client faked. No service, no socket, no network.
"""

from __future__ import annotations

import asyncio
import unittest

from intergen.interfaces.types import Message, MessageRole, RouteResult
from intergen.router import ConversationRouter
from intergen.web_server import ConnectionContext, WebServer


# ── doubles (the shape the neighbouring web tests use) ─────────────────────
class _RecordingWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    def of_type(self, type_name):
        return [m for m in self.sent if m.get("type") == type_name]


class _ChatReply:
    def __init__(self, text: str):
        self.text = text


class _FakeLLM:
    """The model client, faked: streams a fixed grounded answer."""

    _endpoint = "http://127.0.0.1:8080/v1/chat/completions"

    def __init__(self, tokens, chat_text=""):
        self._tokens = list(tokens)
        self._chat_text = chat_text
        self.prompts: list[list[Message]] = []

    def build_system_messages(self, query_type="general", with_tools=True):
        return [Message(role=MessageRole.SYSTEM, content="system prompt")]

    def stream_with_tools(self, messages, tools=None, image_data=None,
                          max_tokens=None):
        self.prompts.append(list(messages))
        yield from self._tokens

    def chat(self, messages, max_tokens=None):
        return _ChatReply(self._chat_text)


# The grounded answer the model composes from the cached data. Plain prose: it
# names no command, claims no action and offers nothing, so no honesty screen
# has anything to rewrite and the only thing under test is the dispatch.
_ANSWER = ["Nothing is failing ", "and the load is low."]
_CACHED_DATA = "failed services: (none)\nload average: 0.21 0.18 0.14"


def _router_with_system_map_verdict(llm) -> ConversationRouter:
    """A real router with no __init__ run (the partial-router idiom of the
    web tests), detached as the daemon leaves the shared router, whose route()
    returns the streamed system_map decision the real router returns under
    decide_only, and whose state cache holds data to ground the answer on."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "system_map"
    r._memory = None
    r._embedder = None
    r._llm = llm
    r._last_semantic_score = None
    r.detach_conversation()
    r._state_cache = type("_Cache", (), {
        "get_system_map_data": staticmethod(lambda q: _CACHED_DATA)})()
    # router.py: the verdict a system-map query gets under decide_only.
    r.route = lambda user_input, **kw: RouteResult(
        text="", source="system_map", handled=False)
    return r


def _turn(question: str):
    """Run one real message-handler turn and hand back the connection."""
    llm = _FakeLLM(_ANSWER)
    router = _router_with_system_map_verdict(llm)
    server = WebServer(router=router, llm=llm, tools=None, governance=None)
    ctx = ConnectionContext(client_id="c1", source_interface="web",
                            ws=_RecordingWS(),
                            conversation=router.new_conversation())
    asyncio.run(server._handle_client_message(
        ctx, {"type": "message", "content": question}))
    return ctx


# Each is a whole-machine health/state ask — the phrasings the grounded
# system-state route exists to claim.
_QUESTIONS = (
    "is everything ok?",
    "what's running",
    "system status",
    "why is it slow",
    "recent errors",
)


class SystemMapWebTurnDeliversBytesTests(unittest.TestCase):

    def test_a_system_map_turn_on_the_web_surface_delivers_bytes(self):
        """No disposition may send an empty answer to the page."""
        silent = {}
        for question in _QUESTIONS:
            ctx = _turn(question)
            if any(not m.get("content") for m in ctx.ws.of_type("response")):
                silent[question] = ctx.ws.sent
        self.assertEqual(
            silent, {},
            "a system_map turn delivered an empty response frame, so the page "
            "clears the thinking indicator and shows nothing: "
            f"{silent}")

    def test_a_system_map_turn_is_streamed_and_ends_on_a_terminal_frame(self):
        """The verdict carries no text, so the turn must be generated by the
        streaming disposition and closed by its terminal frame."""
        ctx = _turn("is everything ok?")
        self.assertEqual(
            len(ctx.ws.of_type("stream_end")), 1,
            "a system_map turn sent no terminal frame; the thinking indicator "
            f"clears with nothing behind it. frames={ctx.ws.sent}")
        self.assertTrue(
            ctx.ws.of_type("stream_start"),
            f"a system_map turn never started a stream. frames={ctx.ws.sent}")
        end = ctx.ws.of_type("stream_end")[0]
        self.assertTrue(
            (end.get("full_response") or "").strip(),
            f"the terminal frame carried no answer: {end}")
        self.assertFalse(ctx.ws.of_type("error"), ctx.ws.sent)

    def test_a_system_map_turn_records_the_answer_it_delivered(self):
        """The conversation is the model's next prompt, the transcript pane and
        the saved session file at once; an empty assistant entry poisons all
        three."""
        ctx = _turn("is everything ok?")
        pairs = [(m.role, m.content) for m in ctx.conversation.history]
        self.assertEqual(
            len(pairs), 2,
            f"expected one recorded exchange, got {pairs}")
        self.assertEqual(pairs[0][0], MessageRole.USER)
        self.assertEqual(pairs[1][0], MessageRole.ASSISTANT)
        self.assertTrue(
            pairs[1][1].strip(),
            "the conversation recorded an empty assistant answer, so the next "
            "turn's prompt and the session file both carry a blank reply")
        self.assertEqual(
            ctx.conversation.transcript[-1][0], "is everything ok?")
        self.assertTrue(
            ctx.conversation.transcript[-1][1].strip(),
            "the verbatim transcript recorded an empty answer: "
            f"{ctx.conversation.transcript[-1]}")


class AnEmptyFallbackAnswerIsRefusedTests(unittest.TestCase):
    """The fallback must not send an empty response, whatever source reaches it.

    system_map is routed to the stream above, and no other decide-only source
    reaches the fallback without text today. The fallback still refuses one if
    it does — an error frame for the person and an error line naming the source
    — so the next such source fails loudly instead of rendering nothing.
    """

    def test_a_verdict_with_no_text_is_refused_not_sent(self):
        llm = _FakeLLM([])
        router = _router_with_system_map_verdict(llm)
        router.route = lambda user_input, **kw: RouteResult(
            text="", source="direct_answer", handled=True)
        server = WebServer(router=router, llm=llm, tools=None, governance=None)
        ctx = ConnectionContext(client_id="c1", source_interface="web",
                                ws=_RecordingWS(),
                                conversation=router.new_conversation())
        with self.assertLogs("intergen.web_server", level="ERROR") as logs:
            asyncio.run(server._handle_client_message(
                ctx, {"type": "message", "content": "tell me something"}))

        self.assertEqual(ctx.ws.of_type("response"), [],
                         "an empty response frame was sent to the page")
        errors = ctx.ws.of_type("error")
        self.assertEqual([e.get("code") for e in errors], ["empty_response"],
                         ctx.ws.sent)
        self.assertTrue(errors[0].get("message"))
        self.assertTrue(
            any("direct_answer" in line for line in logs.output),
            f"the error line does not name the source: {logs.output}")
        self.assertFalse(
            any(m.role == MessageRole.ASSISTANT and not (m.content or "").strip()
                for m in ctx.conversation.history),
            "an empty assistant answer was recorded in the conversation")


if __name__ == "__main__":
    unittest.main()

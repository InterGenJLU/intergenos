# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The browser transcript records each exchange once, and the model is asked
the question once.

The defect: the browser message handler appended the person's message to the
conversation before routing it, and each delivery path then wrote the exchange
back through the router's ``_append_history`` — whose guard only recognises a
``[user, assistant]`` tail. On a streamed model turn the tail was ``[user]``,
so the write-back appended both messages again, and the transcript append
after ``stream_end`` added the assistant a third time: one question and one
answer were stored as ``[user, user, assistant, assistant]`` and persisted
into the session file. Because ``_build_messages`` places the question after
the history, the model's prompt for that turn carried the question twice.

What is pinned here, one case per delivery disposition (streamed model turn,
fast path, fallback), driven through the real message handler with a real
``ConversationRouter`` whose ``route()`` is replaced by a fixed verdict and
whose model client is faked:

  * the conversation holds EXACTLY ``[user, assistant]`` after one turn — the
    same list the transcript pane and the session file read;
  * the model's prompt carries the question exactly once (streamed turn);
  * a second turn appends exactly one more exchange.
"""

from __future__ import annotations

import asyncio
import unittest

from intergen.interfaces.types import Message, MessageRole, RouteResult
from intergen.router import ConversationRouter
from intergen.web_server import ConnectionContext, WebServer


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
    _endpoint = "http://127.0.0.1:8080/v1/chat/completions"

    def __init__(self, tokens, chat_text="Linux was first released in 1991."):
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


def _router_with_verdict(llm, verdict: RouteResult) -> ConversationRouter:
    """A real router (no __init__), detached as the daemon leaves it, whose
    route() returns a fixed verdict — every write-back and prompt builder is
    the real one."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = None
    r._llm = llm
    r._last_semantic_score = None
    r.detach_conversation()
    r.route = lambda user_input, **kw: verdict
    return r


def _server_and_ctx(llm, router):
    server = WebServer(router=router, llm=llm, tools=None, governance=None)
    ctx = ConnectionContext(client_id="c1", source_interface="web",
                            ws=_RecordingWS(),
                            conversation=router.new_conversation())
    return server, ctx


def _send(server, ctx, text):
    asyncio.run(server._handle_client_message(
        ctx, {"type": "message", "content": text}))


def _pairs(ctx):
    return [(m.role, m.content) for m in ctx.conversation.history]


QUESTION = "What year was Linux first released?"
ANSWER = "Linux was first released in 1991."


class StreamedTurnTests(unittest.TestCase):

    def _turn(self):
        llm = _FakeLLM(["Linux ", "was ", "first ", "released ", "in 1991."])
        router = _router_with_verdict(
            llm, RouteResult(text="", source="llm_freeform", handled=False))
        server, ctx = _server_and_ctx(llm, router)
        _send(server, ctx, QUESTION)
        return llm, server, ctx

    def test_the_transcript_is_exactly_one_exchange(self):
        llm, server, ctx = self._turn()
        self.assertEqual(len(ctx.ws.of_type("stream_end")), 1, ctx.ws.sent)
        self.assertFalse(ctx.ws.of_type("error"), ctx.ws.sent)
        self.assertEqual(_pairs(ctx), [(MessageRole.USER, QUESTION),
                                       (MessageRole.ASSISTANT, ANSWER)])
        # The pane and the session file read this same list.
        self.assertIs(ctx.session_history, ctx.conversation.history)

    def test_the_prompt_carries_the_question_once(self):
        llm, server, ctx = self._turn()
        self.assertEqual(len(llm.prompts), 1)
        contents = [m.content for m in llm.prompts[0]]
        self.assertEqual(contents.count(QUESTION), 1, contents)
        self.assertEqual([m.role for m in llm.prompts[0]],
                         [MessageRole.SYSTEM, MessageRole.USER])

    def test_a_second_turn_adds_exactly_one_exchange(self):
        llm, server, ctx = self._turn()
        llm._tokens = ["Linus ", "Torvalds."]
        _send(server, ctx, "Who created it?")
        self.assertEqual(_pairs(ctx), [
            (MessageRole.USER, QUESTION), (MessageRole.ASSISTANT, ANSWER),
            (MessageRole.USER, "Who created it?"),
            (MessageRole.ASSISTANT, "Linus Torvalds.")])
        contents = [m.content for m in llm.prompts[1]]
        self.assertEqual(contents,
                         ["system prompt", QUESTION, ANSWER, "Who created it?"])


class FastPathTurnTests(unittest.TestCase):

    def test_the_transcript_is_exactly_one_exchange(self):
        llm = _FakeLLM([])
        router = _router_with_verdict(
            llm, RouteResult(text="This machine has 4 CPU cores.",
                             source="cache", handled=True))
        server, ctx = _server_and_ctx(llm, router)
        _send(server, ctx, "How many cores?")
        self.assertEqual(len(ctx.ws.of_type("response")), 1, ctx.ws.sent)
        self.assertEqual(_pairs(ctx), [
            (MessageRole.USER, "How many cores?"),
            (MessageRole.ASSISTANT, "This machine has 4 CPU cores.")])
        self.assertEqual(llm.prompts, [])


class FallbackTurnTests(unittest.TestCase):

    def test_the_transcript_is_exactly_one_exchange(self):
        llm = _FakeLLM([])
        router = _router_with_verdict(
            llm, RouteResult(text="I can read files only through the gated tool.",
                             source="direct_answer", handled=True))
        server, ctx = _server_and_ctx(llm, router)
        _send(server, ctx, "Read my shadow file")
        self.assertEqual(len(ctx.ws.of_type("response")), 1, ctx.ws.sent)
        self.assertEqual(_pairs(ctx), [
            (MessageRole.USER, "Read my shadow file"),
            (MessageRole.ASSISTANT,
             "I can read files only through the gated tool.")])


if __name__ == "__main__":
    unittest.main()

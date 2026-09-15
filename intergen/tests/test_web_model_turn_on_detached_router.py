# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A web model turn on a DETACHED router completes inside the connection's
own conversation binding.

The defect (shipped in R001.2, seen on an installed machine 2026-09-14):
the daemon hands the browser server a router whose own conversation has been
detached, so every read of conversation state must name the conversation it
belongs to. ``route()`` did (``conversation=ctx.conversation``); the model
generation that followed it did not. ``_stream_llm_response`` called the
router's ``_build_messages`` — which reads the bound conversation's history —
outside any binding, ``ConversationUnbound`` was raised, the turn crashed, and
the person saw the red "Something went wrong on my end" banner on every
question that needed the model. The same class sat on every private-router
access after ``route()`` in that file.

What is pinned here:

  * a full model turn — prompt built, tokens streamed, the honesty screens run,
    the answer written back — on a real ``ConversationRouter`` whose
    conversation is DETACHED, driven through the real ``_stream_llm_response``
    with only the model client faked. RED at the base (the turn raises), GREEN
    after (a ``stream_end`` with the model's text reaches the client);
  * the same for the two other prompt builders that read conversation state
    (the system-map synthesis and its no-cache fallback);
  * the regenerate path that runs on a WORKER THREAD (the execution-claim
    screen) — a binding is thread-local, so a binding taken on the event loop
    does not cover it; the worker must bind for itself;
  * the binding does not leak: after the turn the loop thread holds no
    conversation, so the refusal a forgetful caller deserves is intact;
  * every private-router access in ``web_server.py`` is either inside the
    connection's binding, or names its conversation explicitly, or touches no
    conversation state — enumerated by reading the source, so a new access
    cannot be added outside the contract without this test naming it.
"""

from __future__ import annotations

import ast
import asyncio
import threading
import unittest
from pathlib import Path

from intergen import web_server as ws_mod
from intergen.interfaces.types import Message, MessageRole, RouteResult
from intergen.router import ConversationRouter, ConversationUnbound
from intergen.web_server import ConnectionContext, WebServer


# ── doubles ────────────────────────────────────────────────────────────────
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
    """The model client, faked: streams a fixed answer, chats a fixed answer."""
    _endpoint = "http://127.0.0.1:8080/v1/chat/completions"

    def __init__(self, tokens, chat_text="Linux was first released in 1991."):
        self._tokens = list(tokens)
        self._chat_text = chat_text
        self.prompts: list[list[Message]] = []
        self.stream_threads: list[str] = []

    def build_system_messages(self, query_type="general", with_tools=True):
        return [Message(role=MessageRole.SYSTEM, content="system prompt")]

    def stream_with_tools(self, messages, tools=None, image_data=None,
                          max_tokens=None):
        self.prompts.append(list(messages))
        self.stream_threads.append(threading.current_thread().name)
        yield from self._tokens

    def chat(self, messages, max_tokens=None):
        return _ChatReply(self._chat_text)


def _detached_router(llm) -> ConversationRouter:
    """A real router with no __init__ run (the partial-router idiom of the
    conversation-isolation tests), then DETACHED — the state the daemon
    leaves the shared router in."""
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


def _run_model_turn(server, ctx, user_msg, source="llm_freeform"):
    # The message handler appends nothing before it routes: the router's
    # _append_history is the single writer of the conversation on the web
    # surface, so driving the streamed path directly meets the same shape.
    route_result = RouteResult(text="", source=source, handled=False)
    asyncio.run(server._stream_llm_response(ctx, "turn1", user_msg,
                                            route_result))


# ── the turn ───────────────────────────────────────────────────────────────
class DetachedRouterModelTurnTests(unittest.TestCase):

    def test_a_freeform_model_turn_completes_on_a_detached_router(self):
        llm = _FakeLLM(["Linux ", "was ", "first ", "released ", "in 1991."])
        router = _detached_router(llm)
        server, ctx = _server_and_ctx(llm, router)
        # The base raises ConversationUnbound out of _build_messages here.
        _run_model_turn(server, ctx, "What year was Linux first released?")
        ends = ctx.ws.of_type("stream_end")
        self.assertEqual(len(ends), 1, ctx.ws.sent)
        self.assertIn("1991", ends[0]["full_response"])
        self.assertTrue(ends[0]["used_llm"])
        self.assertFalse(ctx.ws.of_type("error"))
        # The exchange was written back into THIS connection's conversation,
        # exactly once (the single-writer regression sits beside this test).
        pairs = [(m.role, m.content) for m in ctx.conversation.history]
        self.assertEqual(pairs, [
            (MessageRole.USER, "What year was Linux first released?"),
            (MessageRole.ASSISTANT, "Linux was first released in 1991.")])

    def test_the_prompt_carries_this_connections_history(self):
        llm = _FakeLLM(["Linus ", "Torvalds."])
        router = _detached_router(llm)
        server, ctx = _server_and_ctx(llm, router)
        ctx.conversation.history.extend([
            Message(role=MessageRole.USER,
                    content="What year was Linux first released?"),
            Message(role=MessageRole.ASSISTANT,
                    content="Linux was first released in 1991."),
        ])
        _run_model_turn(server, ctx, "Who created it?")
        self.assertEqual(len(llm.prompts), 1)
        contents = [m.content for m in llm.prompts[0]]
        self.assertIn("Linux was first released in 1991.", contents)
        self.assertIn("Who created it?", contents)

    def test_the_system_map_builders_run_bound_too(self):
        # With cached data: _build_system_map_messages reads the history.
        llm = _FakeLLM(["12 GB free."])
        router = _detached_router(llm)
        router._state_cache = type("_Cache", (), {
            "get_system_map_data": staticmethod(lambda q: "disk: 12G free")})()
        server, ctx = _server_and_ctx(llm, router)
        _run_model_turn(server, ctx, "How much disk is free?",
                        source="system_map")
        self.assertEqual(len(ctx.ws.of_type("stream_end")), 1, ctx.ws.sent)
        self.assertFalse(ctx.ws.of_type("error"))
        # Cache emptied since the decision: the no-fabricate fallback builds
        # a plain prompt, which reads the history as well.
        llm2 = _FakeLLM(["I cannot see that right now."])
        router2 = _detached_router(llm2)
        router2._state_cache = type("_Cache", (), {
            "get_system_map_data": staticmethod(lambda q: None)})()
        server2, ctx2 = _server_and_ctx(llm2, router2)
        _run_model_turn(server2, ctx2, "How much disk is free?",
                        source="system_map")
        self.assertEqual(len(ctx2.ws.of_type("stream_end")), 1, ctx2.ws.sent)
        self.assertFalse(ctx2.ws.of_type("error"))

    def test_a_regenerate_on_the_worker_thread_is_bound_for_itself(self):
        # The draft claims an execution that never happened, so the honesty
        # screen sends the prompt back to the model on a worker thread. A
        # binding is per thread: the worker must take its own.
        llm = _FakeLLM(["I ran ", "the command and it printed 12 GB free."],
                       chat_text="I have not run anything; ask me to check.")
        router = _detached_router(llm)
        server, ctx = _server_and_ctx(llm, router)
        _run_model_turn(server, ctx, "check my disk")
        ends = ctx.ws.of_type("stream_end")
        self.assertEqual(len(ends), 1, ctx.ws.sent)
        self.assertFalse(ctx.ws.of_type("error"))
        self.assertNotIn("I ran the command", ends[0]["full_response"])

    def test_the_binding_does_not_leak_past_the_turn(self):
        llm = _FakeLLM(["1991."])
        router = _detached_router(llm)
        server, ctx = _server_and_ctx(llm, router)
        _run_model_turn(server, ctx, "When?")
        # A caller that forgets to name its conversation is still refused.
        with self.assertRaises(ConversationUnbound):
            router._conv  # noqa: B018 — the property IS the check


# ── every access, by reading the source ────────────────────────────────────
# Router calls the web server may make OUTSIDE a binding: they either name
# the conversation explicitly (`state=`/positional ConversationState), or
# read nothing that belongs to a conversation.
_EXPLICIT_STATE = {"_append_history", "reset_conversation_state"}
_NO_CONVERSATION_STATE = {
    "route",                      # binds the conversation it is given
    "new_conversation",           # makes one; reads none
    "detach_conversation",
    "bind_conversation",
    "last_route_confidence",      # per-turn router scratch, not a conversation
    "get_status",                 # reads without binding by design
    "_escalation",                # provider config, rebuilt on a panel change
    "_state_cache",               # the system-state cache
    "_SYSTEM_MAP_MAX_TOKENS",     # a class constant
}


def _router_accesses(tree: ast.AST):
    """Every `self._router.<name>` attribute node with its ancestor chain."""
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"
                and node.value.attr == "_router"):
            chain = []
            p = parents.get(id(node))
            while p is not None:
                chain.append(p)
                p = parents.get(id(p))
            yield node, chain


def _is_bound(node: ast.Attribute, chain: list[ast.AST]) -> bool:
    """Inside `with self._router.bind_conversation(...)`, or the callable
    argument of `self._bound(ctx, ...)`."""
    for anc in chain:
        if isinstance(anc, ast.With):
            for item in anc.items:
                call = item.context_expr
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "bind_conversation"):
                    return True
        if isinstance(anc, ast.Call) and any(a is node for a in anc.args):
            # `self._bound(ctx, self._router.x, ...)` on the loop thread, or
            # `<context>.run(self._bound, ctx, self._router.x, ...)` on a
            # worker thread — either way the callable is run by _bound.
            def _is_self_bound(e):
                return (isinstance(e, ast.Attribute) and e.attr == "_bound"
                        and isinstance(e.value, ast.Name)
                        and e.value.id == "self")
            if _is_self_bound(anc.func):
                return True
            if (isinstance(anc.func, ast.Attribute) and anc.func.attr == "run"
                    and anc.args and _is_self_bound(anc.args[0])):
                return True
    return False


class EveryRouterAccessIsCoveredTests(unittest.TestCase):

    def test_every_private_router_access_is_bound_or_names_its_conversation(self):
        src = Path(ws_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        uncovered = []
        for node, chain in _router_accesses(tree):
            name = node.attr
            if name in _NO_CONVERSATION_STATE:
                continue
            if name in _EXPLICIT_STATE:
                call = chain[0] if chain else None
                if (isinstance(call, ast.Call) and call.func is node
                        and (any(k.arg == "state" for k in call.keywords)
                             or len(call.args) >= (2 if name ==
                                                   "_append_history" else 1))):
                    continue
            if _is_bound(node, chain):
                continue
            uncovered.append(f"line {node.lineno}: self._router.{name}")
        self.assertEqual(uncovered, [], "router accesses outside the "
                         "connection's conversation binding:\n"
                         + "\n".join(uncovered))

    def test_the_accesses_the_defect_named_are_still_present_and_bound(self):
        # The fix must bind them, not delete the code paths.
        src = Path(ws_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        seen = {node.attr for node, _ in _router_accesses(tree)}
        for name in ("_build_messages", "_build_system_map_messages",
                     "_grounding_context", "_code_offer_staged",
                     "_regenerate_without_claim",
                     "_regenerate_without_selfoffer",
                     "_regenerate_with_capability_grounding"):
            self.assertIn(name, seen, name)


if __name__ == "__main__":
    unittest.main()

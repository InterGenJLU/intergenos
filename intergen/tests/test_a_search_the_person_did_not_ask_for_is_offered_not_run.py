# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A web search the person did not ask for is offered, never run.

THE DEFECT. On the native dispatch floor every conversational turn is offered
every tool schema (router.py, the eligibility block: native_freeform_schema_
exposure), and the router's tool path executes each call the model emits
through ToolRegistry.execute. web_search is read-only (SafetyTier.AUTO), so the
gate's behaviour matrix lets it run: a search the MODEL decided on ran with no
word from the person, and the query the model composed left the machine for the
search engine. The locked floors search only on a recognised explicit ask or an
accepted offer, because there the code owns every dispatch.

WHO BUILT A CALL is not something the call can say: its source_of_request is
the model's own label (the client takes it from the model's own arguments), so a
call the model decided can carry user_direct. The caller of execute() states the
origin; the default is the model, so a caller that says nothing is gated.

WHAT THE FIX DOES, AND WHERE. The registry runs a web_search the model built only
when the turn carries the person's word for a search: a SearchAsk the router
makes from its own recogniser of an explicit ask or its resolver of an accepted
offer, held for that turn only. Without one the call is held: nothing is sent,
the audit record and the glass row say why, and the person is offered the search
in the code's words with the question staged, so "yes" runs it.

WHAT IS PROVED HERE: the registry's gate with a recording search tool in place of
the real one (no query leaves the machine from a unit run); the console path
through route() and the compound seam with a recording model; the browser's
streaming path through the real _stream_llm_response; the locked floor's turns,
which must read the same as before; and a census of every execute() call site,
which fails when one does not state its origin.

WHAT IS NOT PROVED HERE: a real model deciding to search on a real machine (the
daemon firing does that), and the 2B and 35B lanes, which serve the locked floor.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import threading
import unittest
from unittest import mock

from intergen import tool_registry as registry_module
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import (
    LLMResponse, Message, MessageRole, RouteResult, SafetyTier, ToolCall,
    ToolResult, ToolSchema,
)
from intergen.tool_registry import ToolRegistry

INTERGEN_DIR = pathlib.Path(__file__).resolve().parents[1]

#: The words the code offers a held search in (router._offer_a_held_search).
OFFER_WORDS = "Want me to look it up?"
#: The locked floor's own live-data offer, verbatim, which must not move.
LOCKED_OFFER = ("That's live data I can't know from memory — but I can search "
                "the web for it right now. Want me to look it up?")

#: A conversational question the model may decide to search on.
CONVERSATIONAL = "who won the world cup in 1998?"
#: The recognised explicit word orders (the recogniser's own; see router.py
#: _recognised_web_dispatch and _web_search_target).
EXPLICIT_ASKS = (
    ("search the web for the weather in Paris", "the weather in Paris"),
    ("web search for the weather in Paris", "the weather in Paris"),
    ("do a web search for the weather in Paris", "the weather in Paris"),
    ("yes, do a web search for the weather in Paris", "the weather in Paris"),
    ("can you web search and see how much a chippendale dining table sells for?",
     "how much a chippendale dining table sells for"),
)
FORBIDS_TOOLS = (CONVERSATIONAL + " Do not use tools, run commands, access "
                 "files, or contact external services.")


def _fix(name):
    """A name the fix adds to the registry module, or a failure naming it."""
    value = getattr(registry_module, name, None)
    if value is None:
        raise AssertionError(f"intergen.tool_registry has no {name}")
    return value


class _RecordingSearch(BaseTool):
    """Stands in for web_search: records what it would have sent, sends nothing."""

    def __init__(self):
        self.ran: list[dict] = []

    @property
    def name(self):
        return "web_search"

    @property
    def description(self):
        return "Search the web."

    @property
    def schema(self):
        return ToolSchema(
            name="web_search", description="Search the web.",
            parameters={"type": "object",
                        "properties": {"query": {"type": "string"},
                                       "num_results": {"type": "integer"}},
                        "required": ["query"]},
            safety_tier=SafetyTier.AUTO)

    def execute(self, arguments):
        self.ran.append(dict(arguments))
        return ToolResult(call_id="", name="web_search",
                          content=f"1. A result about {arguments.get('query')}")


class _RecordingClock(BaseTool):
    """Another read-only tool, which the gate must leave alone."""

    def __init__(self):
        self.ran: list[dict] = []

    @property
    def name(self):
        return "get_time"

    @property
    def description(self):
        return "Tell the time."

    @property
    def schema(self):
        return ToolSchema(name="get_time", description="Tell the time.",
                          parameters={"type": "object", "properties": {}},
                          safety_tier=SafetyTier.AUTO)

    def execute(self, arguments):
        self.ran.append(dict(arguments))
        return ToolResult(call_id="", name="get_time", content="12:00")


def _model_search(query="1998 world cup winner", **extra):
    """A web_search exactly as the model emits one; the label is the model's."""
    arguments = {"query": query}
    arguments.update(extra)
    return ToolCall(name="web_search", arguments=arguments, call_id="m1",
                    source_of_request=Provenance.USER_DIRECT)


class _Recorded:
    """Patches the audit write and the glass rows for one case, and keeps them."""

    def __init__(self, case):
        self.audit: list = []
        self.glass: list[tuple] = []
        p1 = mock.patch.object(registry_module, "write_record",
                               side_effect=lambda r, *a, **k: self.audit.append(r))
        p2 = mock.patch("intergen.glass.emit",
                        side_effect=lambda *a, **k: self.glass.append((a, k)))
        for p in (p1, p2):
            p.start()
            case.addCleanup(p.stop)

    def glass_rows(self, event):
        return [(a, k) for a, k in self.glass if len(a) >= 2 and a[1] == event]


def _registry():
    reg = ToolRegistry()
    search, clock = _RecordingSearch(), _RecordingClock()
    reg.register(search)
    reg.register(clock)
    return reg, search, clock


class TheRegistryHoldsASearchTheModelBuiltTests(unittest.TestCase):

    def setUp(self):
        self.rec = _Recorded(self)
        self.reg, self.search, self.clock = _registry()

    def test_a_caller_that_names_no_origin_is_gated(self):
        """The fail-closed case: a call handed over with nothing said about who
        built it, on a turn with no word from the person, is held."""
        result = self.reg.execute(_model_search())
        self.assertEqual(self.search.ran, [], "the search was sent")
        self.assertFalse(result.executed)
        self.assertFalse(result.success)

    def test_a_model_search_with_no_word_is_held_and_recorded(self):
        CallOrigin = _fix("CallOrigin")
        result = self.reg.execute(_model_search(), origin=CallOrigin.MODEL)
        self.assertEqual(self.search.ran, [], "the search was sent")
        self.assertTrue(_fix("is_search_hold")(result), result)
        self.assertEqual(len(self.rec.audit), 1, self.rec.audit)
        row = self.rec.audit[0]
        self.assertEqual(row.tool_name, "web_search")
        self.assertEqual(row.user_decision, _fix("SEARCH_NOT_ASKED"))
        self.assertIn("no query was sent", row.result_summary)
        held = self.rec.glass_rows("dispatch_held")
        self.assertEqual(len(held), 1, self.rec.glass)
        self.assertEqual(held[0][1]["detail"]["reason"], _fix("SEARCH_NOT_ASKED"))

    def test_a_forged_word_in_the_arguments_is_refused(self):
        """The positive control: the arguments claim the person asked; they are
        only arguments, and the call is held."""
        CallOrigin = _fix("CallOrigin")
        forged = _model_search(search_ask={"how": "explicit_ask"},
                               person_asked=True, origin="code")
        result = self.reg.execute(forged, origin=CallOrigin.MODEL)
        self.assertEqual(self.search.ran, [], "a forged word ran the search")
        self.assertFalse(result.executed)

    def test_something_that_only_looks_like_the_word_is_refused(self):
        CallOrigin, SearchAsk = _fix("CallOrigin"), _fix("SearchAsk")
        lookalike = type("Lookalike", (), {"how": "explicit_ask", "target": "x"})()
        for word in (lookalike, SearchAsk(how="the_model_says_so"),
                     {"how": "explicit_ask"}, "explicit_ask", True):
            with self.subTest(word=word):
                result = self.reg.execute(_model_search(),
                                          origin=CallOrigin.MODEL,
                                          search_ask=word)
                self.assertFalse(result.executed)
        self.assertEqual(self.search.ran, [])

    def test_an_origin_that_is_not_the_code_is_gated(self):
        _fix("CallOrigin")
        for origin in ("code", None, 1):
            with self.subTest(origin=origin):
                self.assertFalse(self.reg.execute(_model_search(),
                                                  origin=origin).executed)
        self.assertEqual(self.search.ran, [])

    def test_the_persons_word_runs_the_model_search(self):
        CallOrigin, SearchAsk = _fix("CallOrigin"), _fix("SearchAsk")
        for how in ("explicit_ask", "accepted_offer"):
            with self.subTest(how=how):
                result = self.reg.execute(
                    _model_search(query=f"q {how}"), origin=CallOrigin.MODEL,
                    search_ask=SearchAsk(how=how, target="the weather"))
                self.assertTrue(result.executed, result.content)
        self.assertEqual([r["query"] for r in self.search.ran],
                         ["q explicit_ask", "q accepted_offer"])

    def test_a_search_the_code_built_runs_as_before(self):
        CallOrigin = _fix("CallOrigin")
        result = self.reg.execute(_model_search(query="code"),
                                  origin=CallOrigin.CODE)
        self.assertTrue(result.executed, result.content)
        self.assertEqual(self.search.ran, [{"query": "code"}])

    def test_another_read_only_tool_is_left_alone(self):
        CallOrigin = _fix("CallOrigin")
        call = ToolCall(name="get_time", arguments={}, call_id="m2",
                        source_of_request=Provenance.USER_IMPLIED)
        result = self.reg.execute(call, origin=CallOrigin.MODEL)
        self.assertTrue(result.executed, result.content)
        self.assertEqual(self.clock.ran, [{}])


def _router(lock_dispatch: bool, emits=None):
    """A real router over a real registry, the shipped intents (keyword layer,
    no embedder) and a recording model that emits ``emits`` on the tool path."""
    from intergen.intents import register_all_intents
    from intergen.llm import LLMRouter
    from intergen.router import ConversationRouter
    from intergen.semantic import SemanticMatcher

    reg, search, _clock = _registry()
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    router = ConversationRouter(tool_registry=reg, semantic_matcher=matcher,
                                llm=LLMRouter(config=None),
                                lock_dispatch=lock_dispatch)
    model = {"offered": []}

    def _stream_with_tools(messages, *, tools=(), **kwargs):
        model["offered"].append([getattr(t, "name", t) for t in (tools or [])])
        if emits is not None:
            yield emits()
        else:
            yield "France won it."

    def _chat(messages, **kwargs):
        return LLMResponse(text="France won it.", model="stand-in")

    def _continue(messages, tool_call, tool_result, **kwargs):
        return LLMResponse(text=f"From the search: {tool_result}"[:80],
                           model="stand-in")

    router._llm.stream_with_tools = _stream_with_tools
    router._llm.stream = lambda messages, **k: iter(["France won it."])
    router._llm.chat = _chat
    router._llm.continue_after_tool_call = _continue
    return router, search, model


class TheConsoleOffersWhatItHeldTests(unittest.TestCase):
    """route() on the native floor, the path the desktop bus drives."""

    def setUp(self):
        self.rec = _Recorded(self)

    def test_a_conversational_turn_gets_the_offer_and_no_search(self):
        router, search, model = _router(False, emits=_model_search)
        result = router.route(CONVERSATIONAL)
        self.assertTrue(model["offered"], "the turn never reached the tool path")
        self.assertEqual(search.ran, [], "the model's query was sent")
        self.assertIn(OFFER_WORDS, result.text)
        self.assertEqual(router._conv.pending_search_offer,
                         router._semantic._normalize_input(CONVERSATIONAL))
        self.assertTrue(self.rec.glass_rows("dispatch_held"))
        self.assertTrue(self.rec.glass_rows("held_search_offer"))

    def test_yes_to_the_offer_runs_the_search(self):
        router, search, _model = _router(False, emits=_model_search)
        router.route(CONVERSATIONAL)
        self.assertEqual(search.ran, [])
        router.route("yes")
        self.assertEqual(len(search.ran), 1, search.ran)
        self.assertIn("world cup", search.ran[0]["query"])

    def test_an_explicit_ask_still_searches(self):
        for sentence, target in EXPLICIT_ASKS:
            with self.subTest(sentence=sentence):
                router, search, _model = _router(False, emits=_model_search)
                router.route(sentence)
                self.assertEqual(len(search.ran), 1, (sentence, search.ran))

    def _model_answers(self, sentence):
        """route() an ask with the code's own dispatch declined, so the model's
        call is the one that reaches the registry. The code dispatches an
        explicit ask two ways: its intent's tool, and the explicit-search
        route that runs ahead of the model (_try_explicit_web_search); both
        are declined."""
        router, search, model = _router(False, emits=_model_search)
        with mock.patch.object(type(router), "_execute_tool_for_intent",
                               lambda self, *a, **k: (None, None)), \
                mock.patch.object(type(router), "_try_explicit_web_search",
                                  lambda self, *a, **k: None):
            router.route(sentence)
        return router, search, model

    def test_an_explicit_ask_the_model_answers_still_searches(self):
        for sentence, _target in EXPLICIT_ASKS:
            with self.subTest(sentence=sentence):
                _router_, search, model = self._model_answers(sentence)
                self.assertTrue(model["offered"], sentence)
                self.assertEqual(len(search.ran), 1, (sentence, search.ran))

    def test_the_recogniser_writes_the_word_for_an_explicit_ask(self):
        for sentence, target in EXPLICIT_ASKS:
            with self.subTest(sentence=sentence):
                router, _search, _model = self._model_answers(sentence)
                read = getattr(router, "turn_search_ask", lambda: None)()
                self.assertEqual(getattr(read, "how", None), "explicit_ask")
                self.assertEqual(getattr(read, "target", None), target)

    def test_a_turn_that_forbids_tools_is_unchanged(self):
        router, search, model = _router(False, emits=_model_search)
        router.route(FORBIDS_TOOLS)
        self.assertEqual(search.ran, [])
        self.assertTrue(all(offer == [] for offer in model["offered"]),
                        model["offered"])

    def test_the_compound_seam_holds_it_too(self):
        router, search, _model = _router(False, emits=_model_search)
        result = router._route_single(CONVERSATIONAL)
        self.assertEqual(search.ran, [])
        self.assertIn(OFFER_WORDS, result.text)

    def test_the_word_is_absent_on_a_fresh_turn_and_does_not_carry(self):
        router, search, _model = _router(False, emits=_model_search)
        read = getattr(router, "turn_search_ask", None)
        self.assertTrue(callable(read), "the router keeps no reading of the turn")
        self.assertIsNone(read())
        router.route(EXPLICIT_ASKS[0][0])
        self.assertIsNotNone(read())
        router.route(CONVERSATIONAL)
        self.assertIsNone(read())
        self.assertEqual(len(search.ran), 1, search.ran)

    def test_the_word_is_the_routing_threads_own(self):
        router, _search, _model = _router(False, emits=_model_search)
        router.route(EXPLICIT_ASKS[0][0])
        read = getattr(router, "turn_search_ask", lambda: "no reading kept")
        seen = []
        other = threading.Thread(target=lambda: seen.append(read()))
        other.start()
        other.join()
        self.assertEqual(seen, [None])
        self.assertIsNotNone(read())


class TheLockedFloorIsUnchangedTests(unittest.TestCase):
    """The 2B and 35B floors: the same answers as before the gate."""

    def setUp(self):
        self.rec = _Recorded(self)

    def test_a_live_data_question_gets_the_same_offer(self):
        router, search, model = _router(True, emits=_model_search)
        result = router.route("what is the current price of bitcoin?")
        self.assertEqual(result.text, LOCKED_OFFER)
        self.assertEqual(search.ran, [])
        self.assertEqual(model["offered"], [])
        router.route("yes")
        self.assertEqual(len(search.ran), 1, search.ran)

    def test_an_explicit_ask_searches_as_before(self):
        for sentence, target in EXPLICIT_ASKS:
            with self.subTest(sentence=sentence):
                router, search, model = _router(True, emits=_model_search)
                router.route(sentence)
                self.assertEqual(search.ran, [{"query": target}])
                self.assertEqual(model["offered"], [])


class _WS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)


class _StreamingModel:
    _endpoint = "http://127.0.0.1:8080/v1/chat/completions"

    def __init__(self, items):
        self._items = list(items)

    def build_system_messages(self, query_type="general", with_tools=True):
        return [Message(role=MessageRole.SYSTEM, content="system prompt")]

    def stream_with_tools(self, messages, tools=None, image_data=None,
                          max_tokens=None):
        yield from self._items

    def chat(self, messages, max_tokens=None):
        return LLMResponse(text="ok", model="stand-in")

    def continue_after_tool_call(self, messages, tool_call, tool_result, **kw):
        return LLMResponse(text="(the search came back)", model="stand-in")


class TheBrowserStreamOffersWhatItHeldTests(unittest.TestCase):
    """The real _stream_llm_response with the model's call on the stream."""

    def setUp(self):
        self.rec = _Recorded(self)

    def _turn(self, **kwargs):
        from intergen.router import ConversationRouter
        from intergen.web_server import ConnectionContext, WebServer
        model = _StreamingModel([_model_search()])
        router = ConversationRouter.__new__(ConversationRouter)
        router._max_history = 20
        router._record = lambda *a, **k: None
        router._current_query_type = "general"
        router._memory = None
        router._embedder = None
        router._llm = model
        router._last_semantic_score = None
        router.detach_conversation()
        reg, search, _clock = _registry()
        server = WebServer(router=router, llm=model, tools=reg, governance=None)
        ctx = ConnectionContext(client_id="c1", source_interface="web", ws=_WS(),
                                conversation=router.new_conversation())
        asyncio.run(server._stream_llm_response(
            ctx, "turn1", CONVERSATIONAL,
            RouteResult(text="", source="llm_tools", handled=False), **kwargs))
        return ctx, search

    def test_the_streamed_model_search_is_offered_not_run(self):
        ctx, search = self._turn()
        self.assertEqual(search.ran, [], "the model's query was sent")
        tokens = "".join(m.get("token", "") for m in ctx.ws.sent
                         if m.get("type") == "stream_token")
        self.assertIn(OFFER_WORDS, tokens)
        self.assertEqual(ctx.conversation.pending_search_offer, CONVERSATIONAL)

    def test_the_persons_word_runs_it_on_the_stream(self):
        SearchAsk = _fix("SearchAsk")
        _ctx, search = self._turn(search_ask=SearchAsk(how="explicit_ask",
                                                       target="x"))
        self.assertEqual(len(search.ran), 1, search.ran)


def _execute_sites(source: str, filename: str = "<source>"):
    """Every call that reaches ToolRegistry.execute in ``source``: a direct
    ``<...>._tools.execute(...)`` call, or a call handed ``<...>._tools.execute``
    as its first argument (the executor and context hops). Yields (line,
    keywords)."""
    tree = ast.parse(source, filename)

    def _is_registry_execute(node) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == "execute"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr in ("_tools", "tools", "_registry",
                                        "registry", "tool_registry"))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_registry_execute(node.func) or (
                node.args and _is_registry_execute(node.args[0])):
            yield node.lineno, {k.arg: k.value for k in node.keywords if k.arg}


def _origin_named(value) -> str:
    return value.attr if isinstance(value, ast.Attribute) else ast.dump(value)


class EveryExecuteSiteStatesItsOriginTests(unittest.TestCase):
    """The census: every place outside the tests that hands a call to the
    registry says who built it, and exactly the two model paths say the model."""

    def _census(self):
        sites = []
        for path in sorted(INTERGEN_DIR.rglob("*.py")):
            if "tests" in path.relative_to(INTERGEN_DIR).parts:
                continue
            text = path.read_text(encoding="utf-8")
            for line, keywords in _execute_sites(text, str(path)):
                sites.append((path.relative_to(INTERGEN_DIR).as_posix(), line,
                              keywords))
        return sites

    def test_every_site_states_its_origin(self):
        sites = self._census()
        missing = [(f, n) for f, n, kw in sites if "origin" not in kw]
        self.assertEqual(missing, [], "an execute() site names no origin")
        self.assertEqual(len(sites), 6, [(f, n) for f, n, _kw in sites])

    def test_exactly_the_two_model_paths_say_the_model(self):
        model = sorted(f for f, _n, kw in self._census()
                       if "origin" in kw and _origin_named(kw["origin"]) == "MODEL")
        self.assertEqual(model, ["router.py", "web_server.py"])

    def test_the_census_sees_a_site_that_states_nothing(self):
        """The control: a site with no origin is found and reported."""
        planted = ("def f(self, call):\n"
                   "    return self._tools.execute(call, trust_state=None)\n"
                   "def g(self, call, ctx):\n"
                   "    return ctx.run(self._tools.execute, call)\n")
        sites = list(_execute_sites(planted))
        self.assertEqual([line for line, _kw in sites], [2, 4])
        self.assertTrue(all("origin" not in kw for _line, kw in sites))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A turn's own tool prohibition must survive the turn that follows it.

WHAT THE CODE PROMISES. ``_route_impl`` reads the whole turn once, at entry,
into ``self._turn_forbids_tools`` (router.py:2577), and its own comment states
the scope:

    Reset on EVERY turn, unconditionally, so a prohibition can never
    outlive the turn that asked for it and silence the next one.

``_execute_tool_for_intent`` (router.py:6360) is the chokepoint all three
code-owned dispatch paths — keyword, semantic, deterministic fallback — pass
through, and its comment states the rule it enforces there:

    The sentence governs the TURN, so it governs every dispatch in it.

WHERE THE SCOPE IS NOT A TURN. The reading is an INSTANCE attribute of a router
that every browser connection and the desktop bus share. The conversation a
turn belongs to is held per THREAD (``_conversation_binding``, router.py:2224),
and ``_conv``'s own docstring says why: "route() runs in a worker thread, and a
turn abandoned at the server deadline keeps running there. Without the thread
scope that abandoned turn would go on writing into whichever conversation was
bound next." The prohibition reading was never given that scope.

The abandoned turn is not hypothetical. ``_await_route_within_deadline``
(web_server.py:1135) raises TimeoutError once the server has spent
SERVER_ROUTE_DEADLINE_S = 20.0 s of its own working time on a turn, and
``_handle_client_message`` returns from INSIDE ``async with self._route_lock``
(web_server.py:1286) — so the lock that serializes route() across connections is
released while the executor thread is still inside route(). The deadline's own
docstring says the rest: "route() runs in a worker thread and cannot be
interrupted, so on a timeout that thread runs on and its eventual result is
discarded. Any state route() mutates on the way is still mutated." Twenty
seconds is routine on the tier this ships for: one embedding of one sentence was
measured at 57 s on this class of machine (router.py:3316-3318).

WHAT IS PINNED HERE. One shared router, two conversations, two turns. Turn A
forbids tools and is still inside ``_route_impl`` — held in the keyword matcher,
which is where the real seconds go — when turn B, which forbids nothing, runs its
own turn to completion on another thread and rewrites the reading. Turn A then
reaches the dispatch chokepoint.

  * the abandoned turn dispatches no tool — RED at this snapshot: it reads turn
    B's reading and runs the tool its own sentence forbade;
  * the reading turn A's thread sees after turn B has finished is still turn A's
    reading — RED at this snapshot: it is False.

Two controls keep a zero honest: the same harness with no second turn dispatches
for an ordinary sentence (so the instrument can see a dispatch at all) and
withholds for the forbidding sentence (so the race, not the harness, is what
breaks it).

WHAT IS NOT PROVED HERE, STATED PLAINLY. No daemon, no browser, no socket and no
model server ran. The 20-second deadline is not waited out — the hold stands in
for the seconds, and the two threads stand in for the executor thread and the
next turn's. P0 decomposition is switched off because the split is not the seam
under test (it is covered by test_turn_text_forbids_tools_on_every_path.py); the
prohibition reading itself is taken by the real ``turn_forbids_tools`` on the
real sentence. Proving the deadline end to end belongs to the installed-system
gate tier.
"""

from __future__ import annotations

import threading
import unittest
from unittest import mock

import intergen.router as router_mod
from intergen.conversation_state import new_conversation_state
from intergen.decomposer import DecomposedQuery
from intergen.interfaces.types import AnswerLinkage, RouteResult, ToolResult
from intergen.router import ConversationRouter

# The browser battery's own prohibition wording, on a state question that the
# keyword path carries.
FORBIDDING_TURN = ("Tell me what services are running. Do not use tools, run "
                   "commands, access files, or contact external services.")
# The same ask with the prohibition removed — the anti-starvation control that
# proves the keyword path under test really does dispatch.
CONTROL_TURN = "Tell me what services are running."
# The next message on the shared router. It forbids nothing, which is the whole
# point, and it is an ordinary message a second tab would plausibly send.
ORDINARY_TURN = "what is my hostname"
# The thread turn A runs on, named so a dispatch can be attributed to its turn.
TURN_A_THREAD = "turn-a"

_WAIT_TIMEOUT_S = 10.0


class _Match:
    """The shape the keyword layer returns."""

    def __init__(self, intent_id=None, tool_name=None, score=0.0):
        self.intent_id = intent_id
        self.tool_name = tool_name
        self.score = score
        self.runner_up_score = 0.0


class _RecordingTools:
    """Fake ToolRegistry that records every dispatch it is asked to run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (thread name, tool, arguments) — the thread name says WHICH TURN
        # asked for the dispatch, which is the whole question here.
        self.dispatches: list[tuple[str, str, str]] = []

    # -- the surface _route_impl and _execute_tool_for_intent touch --
    def get_tool(self, name):
        return object()          # non-None so the dispatch proceeds

    def get_all_names(self):
        return ["run_command"]   # no web_search → the search intercept declines

    def get_tool_schemas(self):
        return []

    def execute(self, call, *, ingress_tracker=None, trust_state=None,
                review_callback=None, origin=None, search_ask=None):
        with self._lock:
            self.dispatches.append((threading.current_thread().name,
                                    call.name, str(call.arguments)))
        return ToolResult(call_id=getattr(call, "call_id", ""),
                          name=call.name, content="ok", success=True)

    def by_turn(self, thread_name: str) -> list[tuple[str, str, str]]:
        """Every dispatch asked for by the turn running on that thread."""
        with self._lock:
            return [d for d in self.dispatches if d[0] == thread_name]


class _StubLLM:
    """No model. Nothing here is reached on the paths under test."""

    def stream_with_tools(self, messages, tools=None):
        yield "stub"

    def stream(self, *a, **k):
        yield "stub"

    def generate(self, *a, **k):
        return "stub"

    def build_system_messages(self, *a, **k):
        return []

    def _strip_filler(self, text):
        return text


def _router(tools: _RecordingTools, matcher_hook) -> ConversationRouter:
    """A bare router that reaches the real keyword dispatch chokepoint.

    ``matcher_hook(text)`` stands in for the keyword layer: it is where the real
    turn spends its seconds, and it is called on the routing thread.
    """
    r = ConversationRouter.__new__(ConversationRouter)
    r._lock_dispatch = False
    r._tools = tools
    r._llm = _StubLLM()
    r._metrics = None
    r._memory = None
    r._state_cache = None
    r._embedder = None
    r._hardware_tier = None
    r._max_history = 10
    r._review_callback = None
    r._wiki_citations = None
    r._conversation_binding = threading.local()
    # Detached, exactly as the web server's router is: every turn must name the
    # conversation it belongs to.
    r._bound_conversation = None

    sem = mock.Mock()
    sem._normalize_input.side_effect = lambda x: x
    sem._match_keywords.side_effect = matcher_hook
    sem._match_embeddings.side_effect = lambda _t: _Match(score=0.0)
    sem.get_intent_count.return_value = 0
    r._semantic = sem
    return r


_NO_SPLIT = DecomposedQuery(is_compound=False, action_count=1,
                            needs_decomposition=False, sub_queries=[])


def _patches(r: ConversationRouter):
    """Switch off everything that is not the seam, the way the neighbouring
    prohibition suite does: the split (covered elsewhere), the model paths, and
    the turn record. The keyword path itself is NOT replaced — it is the path
    under test."""
    return (
        mock.patch.object(router_mod, "analyze_query",
                          return_value=_NO_SPLIT),
        mock.patch.object(type(r), "_classify_query_type",
                          return_value="general"),
        mock.patch.object(type(r), "_try_semantic_match",
                          return_value=RouteResult(handled=False)),
        mock.patch.object(type(r), "_try_deterministic_fallback",
                          return_value=RouteResult(handled=False)),
        mock.patch.object(type(r), "_try_file_lifecycle", return_value=None),
        mock.patch.object(type(r), "_try_llm_tools",
                          return_value=RouteResult(handled=False)),
        mock.patch.object(type(r), "_try_llm_freeform",
                          return_value=RouteResult(
                              text="ff", source="llm_freeform", handled=True,
                              answer_linkage=AnswerLinkage(
                                  kind="code", renderer="test"))),
        mock.patch.object(type(r), "_template_synthesis",
                          return_value="services: ok"),
        mock.patch.object(type(r), "_append_history", return_value=None),
        mock.patch.object(type(r), "_record", return_value=None),
        mock.patch.object(type(r), "_extract_arguments",
                          return_value={"command": "systemctl"}),
    )


class _Scenario:
    """One shared router, two conversations, two turns — turn A held inside its
    matcher while turn B runs to completion on another thread."""

    def __init__(self) -> None:
        self.tools = _RecordingTools()
        self.inside = threading.Event()     # turn A is inside its turn
        self.release = threading.Event()    # turn A may go on
        self.turn_a_thread: int | None = None
        self.flag_seen_by_turn_a: list[bool] = []
        self.turn_a_result = None
        self.turn_b_result = None
        self.router = _router(self.tools, self._match)

    def _match(self, text):
        if threading.get_ident() != self.turn_a_thread:
            return _Match()                     # turn B matches nothing
        if not self.inside.is_set():
            self.inside.set()
            self.release.wait(timeout=_WAIT_TIMEOUT_S)
            self.flag_seen_by_turn_a.append(
                getattr(self.router, "_turn_forbids_tools", False))
        return _Match("services_status", "run_command")

    def run(self) -> None:
        r = self.router
        conv_a = new_conversation_state()
        conv_b = new_conversation_state()
        results: dict[str, object] = {}

        def turn_a() -> None:
            self.turn_a_thread = threading.get_ident()
            results["a"] = r.route(FORBIDDING_TURN, conversation=conv_a,
                                   decide_only=True)

        stack = _patches(r)
        for p in stack:
            p.start()
        try:
            worker = threading.Thread(target=turn_a, name=TURN_A_THREAD)
            worker.start()
            if not self.inside.wait(timeout=_WAIT_TIMEOUT_S):
                self.release.set()
                worker.join(timeout=_WAIT_TIMEOUT_S)
                raise AssertionError(
                    "turn A never reached its keyword matcher; the harness "
                    "never opened the seam, so nothing was measured.")
            # The server deadline has fired by now: the lock is released and the
            # next turn is served while turn A's thread runs on.
            results["b"] = r.route(ORDINARY_TURN, conversation=conv_b,
                                   decide_only=True)
            self.release.set()
            worker.join(timeout=_WAIT_TIMEOUT_S)
            self.turn_b_result = results.get("b")
            self.turn_a_result = results.get("a")
        finally:
            self.release.set()
            for p in reversed(stack):
                p.stop()


def _solo(text: str) -> _RecordingTools:
    """One turn, no second turn, nothing held — the control harness."""
    tools = _RecordingTools()
    r = _router(tools, lambda _t: _Match("services_status", "run_command"))
    stack = _patches(r)
    for p in stack:
        p.start()
    try:
        r.route(text, conversation=new_conversation_state(), decide_only=True)
    finally:
        for p in reversed(stack):
            p.stop()
    return tools


class TheHarnessControlsTest(unittest.TestCase):
    """A zero below has to mean the prohibition held, not that the harness
    could never dispatch."""

    def test_the_same_ask_without_the_prohibition_does_dispatch(self) -> None:
        """The keyword path under test dispatches for real, so a zero below
        cannot be the harness failing to reach it."""
        tools = _solo(CONTROL_TURN)
        self.assertEqual([(tool, args) for _t, tool, args in tools.dispatches],
                         [("run_command", "{'command': 'systemctl'}")],
                         tools.dispatches)

    def test_a_forbidding_turn_alone_dispatches_nothing(self) -> None:
        """With nothing racing it, the prohibition holds — so the race, not the
        harness, is what breaks it below."""
        tools = _solo(FORBIDDING_TURN)
        self.assertEqual(tools.dispatches, [], tools.dispatches)


class AnAbandonedTurnKeepsItsOwnProhibitionTest(unittest.TestCase):

    def setUp(self) -> None:
        self.scenario = _Scenario()
        self.scenario.run()

    def test_an_abandoned_turn_keeps_its_own_tool_prohibition(self) -> None:
        """The turn that forbade tools must dispatch none, whatever the turn
        after it said."""
        self.assertEqual(
            self.scenario.tools.by_turn(TURN_A_THREAD), [],
            "the abandoned turn dispatched a tool its own sentence forbade: "
            f"{self.scenario.tools.by_turn(TURN_A_THREAD)}")

    def test_the_reading_is_held_per_turn_not_on_the_router(self) -> None:
        """The seam itself: turn A's reading, read from turn A's thread after
        turn B has finished its turn."""
        self.assertEqual(self.scenario.flag_seen_by_turn_a, [True],
                         "turn A read the NEXT turn's prohibition reading, not "
                         "its own")

    def test_the_next_turn_is_still_served_and_still_dispatches(self) -> None:
        """The control INSIDE the same run: the registry was live and recording
        throughout the window, and turn B — which forbids nothing — got its own
        dispatch. Fixing the seam must not take that away."""
        self.assertIsNotNone(self.scenario.turn_b_result)
        self.assertTrue(
            self.scenario.tools.by_turn(threading.main_thread().name),
            f"turn B dispatched nothing: {self.scenario.tools.dispatches}")


if __name__ == "__main__":
    unittest.main()

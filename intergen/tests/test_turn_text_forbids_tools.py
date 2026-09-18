# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A turn whose own text forbids tools is not offered tool schemas.

WHAT THIS IS ABOUT. Tool SCHEMAS are exposed to the model on eligible turns —
deliberately, because starvation is not a trust boundary and the review gate in
ToolRegistry.execute is. But a person who writes

    "Calculate 17 times 23 mentally. Reply with the number only. Do not use
     tools, run commands, access files, or contact external services."

has not failed a scoring threshold; they have given an instruction. Measured
against the running assistant on 2026-09-18, that sentence — the browser
battery's own no-action question — was routed to the tool path on every run, and
the panel then told the person it was looking something up on the one turn where
they had forbidden exactly that.

TWO HALVES ARE PINNED HERE.

* THE READING (intergen.decomposer.turn_forbids_tools), which reuses the same
  negation spans the query splitter uses. A prohibition counts only inside a
  span some negation governs, and only when it names a GENERIC capability. The
  precision cases below are the point of the test: a turn that forbids ONE file
  still gets tools, and a turn that merely mentions tools forbids nothing.

* THE EFFECT (the router's eligibility decision): schemas withheld, the reason
  recorded as "turn_text_forbids_tools" rather than hidden, and the locked floor
  untouched. Withholding is not refusing — the turn falls to the freeform path
  and is answered from the model, which is what was asked for.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import intergen.trace as trace_mod
from intergen.decomposer import turn_forbids_tools
from intergen.interfaces.types import RouteResult
from intergen.router import ConversationRouter
from intergen.tool_registry import ToolRegistry


# The wording that provoked this cut, verbatim from the browser battery.
BATTERY_NO_ACTION = (
    "Calculate 17 times 23 mentally. Reply with the number only. Do not use "
    "tools, run commands, access files, or contact external services.")

FORBIDS = [
    BATTERY_NO_ACTION,
    "Tell me without using any tools.",
    "Answer from memory, never search the web.",
    "Don't run any commands — just explain.",
    "Answer this without going online.",
    "Do not access the internet for this one.",
    "Explain it yourself, do not call any tools.",
    "Never read files when I ask you this.",
]

# Every one of these must keep its tools. They are the cost side of the
# judgement: a wrong True starves a turn that needed a tool.
ALLOWS = [
    "search the web for the ISO 8601 date format",
    "read the file /etc/os-release and tell me the pretty name",
    "list the running services on this machine",
    "install vim without asking, and then open it",
    "don't worry about it",
    "Do not read the file /etc/passwd, read /etc/os-release instead",
    "Do not delete my files",
    "Should I use tools for this?",
    "What is the capital of France?",
    "Can you run commands on this machine?",
    "How do I stop an app from accessing files?",
    "Tell me about the tools you have.",
]


class TurnTextProhibitionReadingTests(unittest.TestCase):
    """The pure reading, with its precision cases."""

    def test_an_explicit_prohibition_is_recognised(self) -> None:
        for q in FORBIDS:
            with self.subTest(q=q):
                self.assertTrue(turn_forbids_tools(q))

    def test_everything_else_keeps_its_tools(self) -> None:
        for q in ALLOWS:
            with self.subTest(q=q):
                self.assertFalse(turn_forbids_tools(q))

    def test_a_prohibition_needs_a_negation_governing_it(self) -> None:
        """The phrase alone is not a prohibition — negation is what makes it one."""
        self.assertFalse(turn_forbids_tools("use tools to answer this"))
        self.assertTrue(turn_forbids_tools("do not use tools to answer this"))

    def test_forbidding_one_named_file_is_not_forbidding_files(self) -> None:
        """The determiner is what separates an object from a capability."""
        self.assertFalse(turn_forbids_tools("do not read the file /etc/passwd"))
        self.assertTrue(turn_forbids_tools("do not read files"))
        self.assertTrue(turn_forbids_tools("do not read any files"))

    def test_the_reading_is_pure(self) -> None:
        """No tier, no daemon, no hardware — the same answer anywhere."""
        self.assertEqual(turn_forbids_tools(BATTERY_NO_ACTION),
                         turn_forbids_tools(BATTERY_NO_ACTION))

    def test_empty_and_odd_input_does_not_raise(self) -> None:
        for q in ["", "   ", "#", "do not", "without", "never"]:
            with self.subTest(q=q):
                self.assertFalse(turn_forbids_tools(q))


def _records(state_dir: str) -> list[dict]:
    p = Path(state_dir) / "intergen" / "decisions.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line]


def _fallthrough_router(*, locked: bool,
                        tools: ToolRegistry | None) -> ConversationRouter:
    """A bare router wired to fall through the fast paths to the eligibility
    decision, with the dispatch lock and tool registry under test control.
    The same shape the M8 eligibility tests use."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._ingress_tracker = mock.Mock()
    r._metrics = None
    r._state_cache = None
    r._memory = None
    r._first_interaction = False
    r._hardware_tier = None
    r._lock_dispatch = locked
    r._tools = tools
    # Present because some wordings reach the history trim on the way to the
    # eligibility decision; the M8 harness never exercised one that did.
    r._max_history = 10
    r._conversation_history = []
    sem = mock.Mock()
    sem._normalize_input.side_effect = lambda x: x
    sem._match_embeddings.return_value = mock.Mock(
        score=0.12, intent_id=None, runner_up_score=0.0)
    r._semantic = sem
    return r


class ForbiddenTurnGetsNoSchemasTests(unittest.TestCase):
    """The effect at the router's eligibility decision."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(setattr, trace_mod, "_tracer", None)
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def _route(self, router: ConversationRouter, user: str) -> dict:
        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "1", "XDG_STATE_HOME": self.state}), \
             mock.patch("intergen.router.analyze_query",
                        return_value=mock.Mock(needs_decomposition=False)), \
             mock.patch.object(type(router), "_classify_query_type",
                               return_value="general"), \
             mock.patch.object(type(router), "_try_keyword_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(router), "_try_deterministic_fallback",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(router), "_try_file_lifecycle",
                               return_value=None), \
             mock.patch.object(type(router), "_try_llm_tools",
                               return_value=RouteResult(
                                   text="", source="llm_tools", handled=False)), \
             mock.patch.object(type(router), "_try_llm_freeform",
                               return_value=RouteResult(
                                   text="", source="llm_freeform", handled=False)), \
             mock.patch.object(type(router), "_record"):
            trace_mod._tracer = None
            router.route(user, decide_only=True)
        return [s for s in _records(self.state)
                if s["name"] == "router.route"][0]["attributes"]

    def test_the_battery_question_is_offered_no_schemas(self) -> None:
        """RED before this change: eligible, with every schema on offer."""
        r = _fallthrough_router(locked=False, tools=self.registry)
        attrs = self._route(r, BATTERY_NO_ACTION)
        self.assertFalse(attrs["eligible_for_tools"])
        self.assertEqual(attrs["tool_schemas_offered"], [])

    def test_the_reason_is_recorded_and_says_why(self) -> None:
        """A withheld offer that does not say why is indistinguishable from a
        bug. The reason names the turn's own text."""
        r = _fallthrough_router(locked=False, tools=self.registry)
        attrs = self._route(r, BATTERY_NO_ACTION)
        self.assertEqual(attrs["eligibility_reason"], "turn_text_forbids_tools")
        self.assertTrue(attrs["turn_text_forbids_tools"])

    def test_every_forbidding_wording_withholds(self) -> None:
        for q in FORBIDS:
            with self.subTest(q=q):
                r = _fallthrough_router(locked=False, tools=self.registry)
                attrs = self._route(r, q)
                self.assertFalse(attrs["eligible_for_tools"])
                self.assertEqual(attrs["tool_schemas_offered"], [])

    def test_an_ordinary_turn_still_gets_every_schema(self) -> None:
        """The widened exposure is untouched for everything else — this is the
        anti-starvation control."""
        for q in ALLOWS:
            with self.subTest(q=q):
                r = _fallthrough_router(locked=False, tools=self.registry)
                attrs = self._route(r, q)
                self.assertTrue(attrs["eligible_for_tools"])
                self.assertTrue(attrs["tool_schemas_offered"])
                self.assertEqual(attrs["eligibility_reason"],
                                 "native_freeform_schema_exposure")
                self.assertFalse(attrs["turn_text_forbids_tools"])

    def test_the_locked_floor_is_byte_identical(self) -> None:
        """On the locked floor nothing is offered either way, and the reason
        stays the floor's own — this change must not rename that state."""
        for q in (BATTERY_NO_ACTION, "read the file /etc/os-release"):
            with self.subTest(q=q):
                r = _fallthrough_router(locked=True, tools=self.registry)
                attrs = self._route(r, q)
                self.assertFalse(attrs["eligible_for_tools"])
                self.assertEqual(attrs["tool_schemas_offered"], [])
                self.assertEqual(attrs["eligibility_reason"],
                                 "locked_floor_code_owned")

    def test_a_forbidden_turn_is_still_answered(self) -> None:
        """Withholding schemas must not withhold the ANSWER. The turn falls to
        the freeform path rather than being refused or dropped."""
        r = _fallthrough_router(locked=False, tools=self.registry)
        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "1", "XDG_STATE_HOME": self.state}), \
             mock.patch("intergen.router.analyze_query",
                        return_value=mock.Mock(needs_decomposition=False)), \
             mock.patch.object(type(r), "_classify_query_type",
                               return_value="general"), \
             mock.patch.object(type(r), "_try_keyword_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_deterministic_fallback",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_file_lifecycle",
                               return_value=None), \
             mock.patch.object(type(r), "_try_llm_tools",
                               return_value=RouteResult(
                                   text="", source="llm_tools", handled=False)), \
             mock.patch.object(type(r), "_try_llm_freeform",
                               return_value=RouteResult(
                                   text="391", source="llm_freeform", handled=True)), \
             mock.patch.object(type(r), "_record"):
            trace_mod._tracer = None
            out = r.route(BATTERY_NO_ACTION)
        self.assertTrue(out.handled)
        self.assertEqual(out.source, "llm_freeform")
        self.assertEqual(out.text, "391")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The prohibition is read once per turn and enforced at the chokepoint.

WHAT THE FIRST READING MISSED. Withholding tool schemas from a turn whose own
text forbids them was decided at route()'s eligibility block. That block is not
on every path to the model. P0 compound decomposition runs BEFORE it:

    route() -> P0 -> _handle_compound -> _route_single -> _try_llm_tools

and _route_single calls _try_llm_tools unconditionally — the same ungated seam
the dispatch lockdown's own gate was written to cover. Measured 2026-09-18 with
the real decomposer and a recording model:

    "Tell me the time and how much memory I have, without using any tools."
        -> split in two, each clause handed all nine tool schemas

Worse, the FIRST clause is "Tell me the time", which carries no prohibition at
all. The fact lives in the whole turn and the split destroys it, so no
per-clause reading can recover it.

THE TWO HALVES PINNED HERE.

* ONE READING, TAKEN BEFORE THE SPLIT. ``_route_impl`` reads the whole turn once
  at entry, before P0, into a per-turn flag that is reset on every turn. The
  eligibility block reuses that flag rather than re-reading.

* ONE GATE, AT THE CHOKEPOINT. ``_try_llm_tools`` returns handled=False when the
  per-turn flag is set OR when the text it was handed forbids tools on its own.
  The second reading is not redundant: the memory-complaint re-route hands
  _route_single an EARLIER turn's text, which never passed through this turn's
  route() at all.

Withholding is not refusing — a gated clause falls through to P4 freeform and is
answered from the model, which is what was asked for.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import intergen.glass as glass_mod
import intergen.trace as trace_mod
from intergen.interfaces.types import HardwareTierLevel, RouteResult
from intergen.router import ConversationRouter
from intergen.tool_registry import ToolRegistry

# A compound whose prohibition sits in the SECOND clause only — the first
# clause, read alone, forbids nothing.
MIXED_COMPOUND = ("Tell me the time and how much memory I have, without using "
                  "any tools.")
# A compound carrying the browser battery's own wording.
BATTERY_COMPOUND = ("What time is it and what is the capital of France? Do not "
                    "use tools, run commands, access files, or contact "
                    "external services.")
# The same compound with the prohibition removed — the anti-starvation control.
CONTROL_COMPOUND = "What time is it and what is the capital of France?"
# The single-sentence turn the first reading already covered.
BATTERY_NO_ACTION = (
    "Calculate 17 times 23 mentally. Reply with the number only. Do not use "
    "tools, run commands, access files, or contact external services.")


class _Msg:
    """The shape _build_messages expects back from the model wrapper."""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class RecordingLLM:
    """A model that runs no model and writes down what it was offered.

    ``calls`` gets one entry per ``stream_with_tools`` call: the number of tool
    schemas handed to it. That number IS the property under test — a clause
    that reaches the model with nine schemas has been offered the tools the
    person forbade, whether or not the model would have used them.
    """

    def __init__(self) -> None:
        self.calls: list[int] = []

    def stream_with_tools(self, messages, tools=None):
        self.calls.append(len(tools or []))
        yield "stub answer"

    def stream(self, *a, **k):
        yield "stub answer"

    def generate(self, *a, **k):
        return "stub answer"

    def build_system_messages(self, *a, **k):
        return [_Msg("system", "stub")]

    def _strip_filler(self, text):
        return text


def _router(registry: ToolRegistry, llm: RecordingLLM) -> ConversationRouter:
    """A bare router that reaches P0 and the tool path for real."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._ingress_tracker = mock.Mock()
    r._metrics = None
    r._state_cache = None
    r._memory = None
    r._first_interaction = False
    r._hardware_tier = HardwareTierLevel.TIER_2
    r._lock_dispatch = False
    r._tools = registry
    r._max_history = 10
    r._conversation_history = []
    r._llm = llm
    r._review_callback = None
    sem = mock.Mock()
    sem._normalize_input.side_effect = lambda x: x
    sem._match_embeddings.return_value = mock.Mock(
        score=0.12, intent_id=None, runner_up_score=0.0)
    r._semantic = sem
    return r


class _StateDirMixin:
    """Every test here gets its own state directory, for the whole test.

    The decision trace and the glass log both write under
    ``XDG_STATE_HOME/intergen``. Two things go wrong if that is left to chance:
    the directory does not exist and every emit logs "glass write failed", and
    ``glass._glass`` is a module-level singleton that resolves its path ONCE,
    so a later test inherits an earlier test's directory after it has been
    cleaned up and logs the same error for a different reason. Create the
    directory, point the environment at it for the whole test, and reset the
    singleton so it resolves afresh. A test whose own output carries errors
    teaches a reader to skim past errors.
    """

    def _state_dir(self) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = tmp.name
        os.makedirs(os.path.join(state, "intergen"), exist_ok=True)
        prior_env = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = state
        def _restore() -> None:
            if prior_env is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = prior_env
            glass_mod._glass = None
        self.addCleanup(_restore)
        glass_mod._glass = None
        return state


class TheCompoundSeamTest(_StateDirMixin, unittest.TestCase):
    """The REAL decomposer runs here — analyze_query is NOT patched. That is
    the whole point: the first reading's own probe patched it to
    needs_decomposition=False, which is exactly why it could not see this."""

    def setUp(self) -> None:
        self.state = self._state_dir()
        self.addCleanup(setattr, trace_mod, "_tracer", None)
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.schema_count = len(self.registry.get_tool_schemas())

    def _run(self, query: str) -> tuple[RouteResult, list[int]]:
        llm = RecordingLLM()
        r = _router(self.registry, llm)
        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "1",
                              "XDG_STATE_HOME": self.state}), \
             mock.patch.object(type(r), "_classify_query_type",
                               return_value="general"), \
             mock.patch.object(type(r), "_try_keyword_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_semantic_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_deterministic_fallback",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_file_lifecycle",
                               return_value=None), \
             mock.patch.object(type(r), "_try_llm_freeform",
                               return_value=RouteResult(
                                   text="ff", source="llm_freeform",
                                   handled=True)), \
             mock.patch.object(type(r), "_record"):
            trace_mod._tracer = None
            res = r.route(query, decide_only=False)
        return res, llm.calls

    def test_the_registry_really_has_schemas_to_offer(self) -> None:
        """Positive control for the instrument: a zero below has to mean the
        gate held, not that there was nothing to hand over."""
        self.assertGreater(self.schema_count, 0)
        res, calls = self._run(CONTROL_COMPOUND)
        self.assertEqual(res.source, "decomposed")
        self.assertEqual(calls, [self.schema_count] * len(calls))
        self.assertGreaterEqual(len(calls), 2)

    def test_a_mixed_compound_hands_no_tools_to_any_clause(self) -> None:
        """RED before this change: [9, 9]."""
        res, calls = self._run(MIXED_COMPOUND)
        self.assertEqual(res.source, "decomposed")
        self.assertEqual(calls, [])

    def test_the_battery_wording_in_a_compound_hands_none_either(self) -> None:
        res, calls = self._run(BATTERY_COMPOUND)
        self.assertEqual(res.source, "decomposed")
        self.assertEqual(calls, [])

    def test_the_control_compound_still_gets_every_schema(self) -> None:
        """The anti-starvation control: an ordinary compound is untouched."""
        _res, calls = self._run(CONTROL_COMPOUND)
        self.assertTrue(calls)
        for handed in calls:
            self.assertEqual(handed, self.schema_count)

    def test_the_single_sentence_turn_is_unchanged(self) -> None:
        """What the first reading already covered must keep working."""
        res, calls = self._run(BATTERY_NO_ACTION)
        self.assertEqual(calls, [])
        self.assertEqual(res.source, "llm_freeform")

    def test_the_forbidden_compound_is_still_answered(self) -> None:
        """Withholding the schemas must not withhold the ANSWER."""
        res, _calls = self._run(MIXED_COMPOUND)
        self.assertTrue(res.handled)
        self.assertTrue(res.text.strip())


class TheChokepointItselfTest(_StateDirMixin, unittest.TestCase):
    """_try_llm_tools called DIRECTLY, the way every present and future caller
    reaches it."""

    def setUp(self) -> None:
        self.state = self._state_dir()
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.schema_count = len(self.registry.get_tool_schemas())
        self.addCleanup(setattr, trace_mod, "_tracer", None)
        trace_mod._tracer = None

    def _call(self, text: str, *, per_turn_flag: bool) -> tuple[RouteResult,
                                                                list[int]]:
        llm = RecordingLLM()
        r = _router(self.registry, llm)
        r._turn_forbids_tools = per_turn_flag
        with mock.patch.object(type(r), "_trail_note"):
            res = r._try_llm_tools(text)
        return res, llm.calls

    def test_the_per_turn_flag_alone_closes_it(self) -> None:
        """The split-clause case: the clause's own text forbids nothing, and
        the flag is the only thing that still knows."""
        res, calls = self._call("Tell me the time", per_turn_flag=True)
        self.assertFalse(res.handled)
        self.assertEqual(calls, [])

    def test_the_handed_text_alone_closes_it(self) -> None:
        """The re-route case: this text never passed through this turn's
        route(), so no per-turn flag was ever set for it."""
        res, calls = self._call(BATTERY_NO_ACTION, per_turn_flag=False)
        self.assertFalse(res.handled)
        self.assertEqual(calls, [])

    def test_an_ordinary_call_is_untouched(self) -> None:
        _res, calls = self._call("list the printers", per_turn_flag=False)
        self.assertEqual(calls, [self.schema_count])

    def test_the_dispatch_lock_still_wins_first(self) -> None:
        """The locked floor's own reason must not be renamed by this gate."""
        llm = RecordingLLM()
        r = _router(self.registry, llm)
        r._lock_dispatch = True
        r._turn_forbids_tools = False
        res = r._try_llm_tools("list the printers")
        self.assertFalse(res.handled)
        self.assertEqual(llm.calls, [])


class TheReadingIsResetEveryTurnTest(_StateDirMixin, unittest.TestCase):
    """A prohibition must never outlive the turn that asked for it."""

    def setUp(self) -> None:
        self.state = self._state_dir()
        self.addCleanup(setattr, trace_mod, "_tracer", None)
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def test_a_forbidding_turn_does_not_silence_the_next_one(self) -> None:
        llm = RecordingLLM()
        r = _router(self.registry, llm)
        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "1",
                              "XDG_STATE_HOME": self.state}), \
             mock.patch.object(type(r), "_classify_query_type",
                               return_value="general"), \
             mock.patch.object(type(r), "_try_keyword_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_semantic_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_deterministic_fallback",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_file_lifecycle",
                               return_value=None), \
             mock.patch.object(type(r), "_try_llm_freeform",
                               return_value=RouteResult(
                                   text="ff", source="llm_freeform",
                                   handled=True)), \
             mock.patch.object(type(r), "_record"):
            trace_mod._tracer = None
            r.route(MIXED_COMPOUND, decide_only=False)
            self.assertTrue(r._turn_forbids_tools)
            r.route(CONTROL_COMPOUND, decide_only=False)
            self.assertFalse(r._turn_forbids_tools)
        self.assertTrue(llm.calls,
                        "the second, ordinary turn was starved of schemas")


if __name__ == "__main__":
    unittest.main()

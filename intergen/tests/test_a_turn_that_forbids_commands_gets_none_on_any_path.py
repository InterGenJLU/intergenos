# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A turn whose own text forbids commands gets none — on any path.

WHAT WAS LEFT OPEN. Withholding the tool schemas stops the MODEL from asking
for a tool. It does nothing about the paths that dispatch without asking the
model at all. Measured 2026-09-18 on the running assistant, reading the
daemon's own per-clause record:

    "What time is it and why is the sky blue? Do not use tools, run commands,
     access files, or contact external services."
        clause 1/2: source='keyword' tools=['run_command']
        clause 2/2: source='llm_freeform' tools=[]

The schemas were correctly withheld from the model for that turn, and the first
clause ran `date` anyway, because the keyword match decides for itself. The
semantic match and the deterministic state fallback decide the same way, and
the "what is my IP" handler runs two fixed commands of its own.

THE RULE PINNED HERE. The sentence governs the TURN, so it governs every
dispatch in it:

  * the keyword, semantic and deterministic-fallback paths all reach the tool
    through one helper, and that helper does not dispatch when the per-turn
    flag is set — it records reason=turn_text_forbids_tools and returns
    nothing, so each caller continues down the ladder exactly as it does for
    any other undispatched intent;
  * the fixed-command helper (the IP answer's ifconfig and dig) is gated the
    same way and yields no reading;
  * a staged action accepted on a turn that also forbids commands is not run
    — a backstop, since an acceptance cannot carry a prohibition today;
  * the compound path needs no rule of its own: each clause walks the same
    helpers, and the reading was taken from the whole turn before the split.

THE INSTRUMENT. Every test here counts calls to ToolRegistry.execute — the one
place a tool actually runs. A zero means nothing ran. Each zero is paired with
a CONTROL, the same text with the prohibition removed, which must still reach
the tool; without that pair a zero could mean the path was never exercised.
The paths under test are NOT patched out: the matcher is asked to supply a
match, and everything from the dispatch decision onwards is the real code.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import intergen.glass as glass_mod
from intergen.interfaces.types import HardwareTierLevel
from intergen.router import ConversationRouter
from intergen.tool_registry import ToolRegistry

FORBIDDING = ("What time is it? Do not use tools, run commands, access files, "
              "or contact external services.")
CONTROL = "What time is it?"
FORBIDDING_COMPOUND = ("What time is it and why is the sky blue? Do not use "
                       "tools, run commands, access files, or contact "
                       "external services.")


class _Result:
    """The shape the dispatch paths read off a ToolResult."""

    def __init__(self, content: str = "Thu Sep 18 02:53:42 PM CDT 2026") -> None:
        self.content = content
        self.model_summary = content
        self.success = True
        self.executed = True
        self.blocked = False
        self.error = None
        self.call_id = "abcd1234"


class _StateDirMixin:
    """Own state directory per test, so glass writes land somewhere real."""

    def _state_dir(self) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "intergen"), exist_ok=True)
        prior = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = tmp.name

        def _restore() -> None:
            if prior is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = prior
            glass_mod._glass = None
        self.addCleanup(_restore)
        glass_mod._glass = None
        return tmp.name


class _RouterMixin(_StateDirMixin):

    def setUp(self) -> None:
        self.state = self._state_dir()
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.executed = []

        def _execute(call, **kwargs):
            self.executed.append(call.name)
            return _Result()
        self.registry.execute = _execute            # the one place a tool runs

    def _router(self, *, forbidden: bool) -> ConversationRouter:
        r = ConversationRouter.__new__(ConversationRouter)
        r._ingress_tracker = mock.Mock()
        r._metrics = None
        r._state_cache = None
        r._memory = None
        r._first_interaction = False
        r._hardware_tier = HardwareTierLevel.TIER_2
        r._lock_dispatch = False
        r._tools = self.registry
        r._max_history = 10
        r._conversation_history = []
        r._llm = mock.Mock()
        r._review_callback = None
        r._route_trail = []
        # _conv is a property; a router built with __new__ makes its own
        # conversation state on first read, which is what the dispatch
        # paths want here.
        r._turn_forbids_tools = forbidden
        sem = mock.Mock()
        sem._normalize_input.side_effect = lambda x: x
        r._semantic = sem
        return r


class TheKeywordPathTest(_RouterMixin, unittest.TestCase):
    """The path that answered the measured turn's first clause."""

    def _match(self, router):
        router._semantic._match_keywords.return_value = mock.Mock(
            intent_id="time_query", tool_name="run_command", score=1.0)

    def test_the_control_really_dispatches(self) -> None:
        """Positive control for the instrument: the zero below has to mean the
        gate held, not that this path never reaches a tool."""
        r = self._router(forbidden=False)
        self._match(r)
        with mock.patch.object(type(r), "_extract_arguments",
                               return_value={"command": "date"}), \
             mock.patch.object(type(r), "_template_synthesis",
                               return_value="It is 2:53 PM."), \
             mock.patch.object(type(r), "_append_history"):
            res = r._try_keyword_match(CONTROL)
        self.assertEqual(self.executed, ["run_command"])
        self.assertTrue(res.handled)

    def test_a_forbidding_turn_runs_nothing(self) -> None:
        r = self._router(forbidden=True)
        self._match(r)
        with mock.patch.object(type(r), "_extract_arguments",
                               return_value={"command": "date"}), \
             mock.patch.object(type(r), "_template_synthesis",
                               return_value="It is 2:53 PM."), \
             mock.patch.object(type(r), "_append_history"):
            res = r._try_keyword_match(FORBIDDING)
        self.assertEqual(self.executed, [])
        self.assertFalse(res.handled)

    def test_the_decline_names_the_reason(self) -> None:
        """The reason is recorded, not buried under a different label: before
        this change an undispatched intent could only be reported as an
        indeterminate argument."""
        r = self._router(forbidden=True)
        self._match(r)
        with mock.patch.object(type(r), "_extract_arguments",
                               return_value={"command": "date"}), \
             mock.patch.object(type(r), "_append_history"):
            r._try_keyword_match(FORBIDDING)
        reasons = [n.get("reason") for n in r._route_trail if "reason" in n]
        self.assertIn("turn_text_forbids_tools", reasons)
        self.assertNotIn("arguments_indeterminate", reasons)


class TheSemanticPathTest(_RouterMixin, unittest.TestCase):

    def _match(self, router):
        router._semantic._match_embeddings.return_value = mock.Mock(
            intent_id="time_query", tool_name="run_command", score=0.91)

    def test_the_control_really_dispatches(self) -> None:
        r = self._router(forbidden=False)
        self._match(r)
        with mock.patch.object(type(r), "_extract_arguments",
                               return_value={"command": "date"}), \
             mock.patch.object(type(r), "_template_synthesis",
                               return_value="It is 2:53 PM."), \
             mock.patch.object(type(r), "_append_history"):
            res = r._try_semantic_match(CONTROL)
        self.assertEqual(self.executed, ["run_command"])
        self.assertTrue(res.handled)

    def test_a_forbidding_turn_runs_nothing(self) -> None:
        r = self._router(forbidden=True)
        self._match(r)
        with mock.patch.object(type(r), "_extract_arguments",
                               return_value={"command": "date"}), \
             mock.patch.object(type(r), "_template_synthesis",
                               return_value="It is 2:53 PM."), \
             mock.patch.object(type(r), "_append_history"):
            res = r._try_semantic_match(FORBIDDING)
        self.assertEqual(self.executed, [])
        self.assertFalse(res.handled)


class TheDeterministicFallbackTest(_RouterMixin, unittest.TestCase):
    """The state-question fast path, which runs a command without any match."""

    def test_the_control_really_dispatches(self) -> None:
        r = self._router(forbidden=False)
        with mock.patch.object(type(r), "_extract_arguments",
                               return_value={"command": "date"}), \
             mock.patch.object(type(r), "_template_synthesis",
                               return_value="It is 2:53 PM."), \
             mock.patch.object(type(r), "_append_history"):
            res = r._try_deterministic_fallback(CONTROL)
        self.assertEqual(self.executed, ["run_command"])
        self.assertTrue(res.handled)

    def test_a_forbidding_turn_runs_nothing(self) -> None:
        r = self._router(forbidden=True)
        with mock.patch.object(type(r), "_extract_arguments",
                               return_value={"command": "date"}), \
             mock.patch.object(type(r), "_template_synthesis",
                               return_value="It is 2:53 PM."), \
             mock.patch.object(type(r), "_append_history"):
            res = r._try_deterministic_fallback(FORBIDDING)
        self.assertEqual(self.executed, [])
        self.assertFalse(res.handled)


class TheFixedCommandPathTest(_RouterMixin, unittest.TestCase):
    """The IP answer's own commands, which never touch the intent helper."""

    def test_the_control_really_runs_the_fixed_command(self) -> None:
        r = self._router(forbidden=False)
        out = r._run_fixed_command("ifconfig")
        self.assertEqual(self.executed, ["run_command"])
        self.assertTrue(out)

    def test_a_forbidding_turn_runs_neither_of_them(self) -> None:
        r = self._router(forbidden=True)
        self.assertIsNone(r._run_fixed_command("ifconfig"))
        self.assertIsNone(r._run_fixed_command("dig +short myip.opendns.com"))
        self.assertEqual(self.executed, [])


class TheStagedActionBackstopTest(_RouterMixin, unittest.TestCase):
    """An acceptance cannot carry a prohibition today; the path is gated anyway."""

    def test_the_control_really_runs_the_staged_action(self) -> None:
        r = self._router(forbidden=False)
        with mock.patch.object(type(r), "_template_synthesis",
                               return_value="done"), \
             mock.patch.object(type(r), "_append_history"):
            res = r._run_staged_action("mkdir -p /tmp/x")
        self.assertEqual(self.executed, ["run_command"])
        self.assertTrue(res.handled)

    def test_a_forbidding_turn_does_not_run_it_and_says_so(self) -> None:
        r = self._router(forbidden=True)
        res = r._run_staged_action("mkdir -p /tmp/x")
        self.assertEqual(self.executed, [])
        self.assertTrue(res.handled)
        self.assertIn("did not run it", res.text)


class TheWholeTurnReadingStillHoldsTest(_RouterMixin, unittest.TestCase):
    """The flag is the whole turn's reading, so a clause that forbids nothing
    on its own is still covered — the property the split destroyed."""

    def test_a_clause_of_a_forbidding_compound_runs_nothing(self) -> None:
        from intergen.decomposer import turn_forbids_tools
        self.assertTrue(turn_forbids_tools(FORBIDDING_COMPOUND))
        self.assertFalse(turn_forbids_tools("What time is it"))
        r = self._router(forbidden=turn_forbids_tools(FORBIDDING_COMPOUND))
        r._semantic._match_keywords.return_value = mock.Mock(
            intent_id="time_query", tool_name="run_command", score=1.0)
        with mock.patch.object(type(r), "_extract_arguments",
                               return_value={"command": "date"}), \
             mock.patch.object(type(r), "_append_history"):
            res = r._try_keyword_match("What time is it")
        self.assertEqual(self.executed, [])
        self.assertFalse(res.handled)


if __name__ == "__main__":
    unittest.main()

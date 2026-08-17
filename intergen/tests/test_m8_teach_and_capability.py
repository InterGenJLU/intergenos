# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M8 wave 6 — teach_gap + capability-question generalization (+ wave-5 rider).

Trace-grounded from the discovery ledger (m8-wave1-ledger/consolidated-ledger.jsonl).

LEG 1 — teach_gap (13 medium; ids dd-guide-0100/0101/0106/0107/0108/0114/0118/0122/
0143/0150, dd-howto-0139/0142, dd-file-0002). A general-knowledge how-to / advice ask
("how do i make a password that's actually secure", "whats a good way to back up my
files", "how do i lock my screen") was answered with an "I don't have current data on
that" hedge. CAUSE (per the diagnostic doctrine — check OUR layer first): OUR injected
instruction in _try_llm_freeform, fired for a diagnostic-classified turn, told the model
to say "I don't have current data" whenever it lacked tool output — over-rotating onto
general-knowledge asks. FIX (our layer): the instruction now scopes the hedge to THIS
machine's CURRENT STATE and directs a teaching answer for a general how-to; the
anti-fabrication guard stays. (Live-9B answer text is the 9B-seat leg; here we pin the
assembled prompt.)

LEG 2 — capability-question generalization (the 6 sf-cap-* latency_outlier entries, all
~120000 ms = the turn timeout: sf-cap-manage-services-0/-1, sf-cap-open-application-0/-1,
sf-cap-read-file-0/-1). A capability QUESTION about a live tool was treated as a command,
entered the dispatch/gate path, and wedged to timeout. The M8-3 web_search intercept is
generalized across the tool surface and, with the wave-3/4 intercepts, unified into ONE
block that runs BEFORE normalization + explain + decomposition — normalization strips the
"can you" frame ("can you search the internet" -> "search the internet"), which both lost
the frame and made the question look like a command (it also silently defeated the wave-3
web intercept end-to-end and preempted the wave-4 pkm one behind explain). Grounded from
the real registry; a real ask ("read my notes file", "open firefox") is not captured.

RIDER (wave 5) — the inline-directory branch honors an explicit location tail ("make a
projects directory in my Downloads folder" -> mkdir -p ~/Downloads/projects, gated,
default_applied reflects it); the home default stays when no location is named.

Execution byte-identical: routing / answer-shaping / prompt-assembly only.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.interfaces.types import SafetyTier, MessageRole
from intergen.router import (ConversationRouter, detect_file_lifecycle_intent,
                             _CAP_Q_FRAME_RE)
from intergen.semantic import SemanticMatcher
from intergen.llm import LLMRouter
from intergen.tool_registry import ToolRegistry

HOME = "/home/tester"


def _native_router():
    reg = ToolRegistry()
    reg.discover_tools()
    return ConversationRouter(
        tool_registry=reg, semantic_matcher=SemanticMatcher(embedder=None),
        llm=LLMRouter(config=None), lock_dispatch=False)


class TeachGapPromptTests(unittest.TestCase):
    """LEG 1: the diagnostic freeform injection scopes the hedge to system CURRENT
    STATE and directs a teaching answer — the cause was fixed at OUR layer."""

    def _injected_instruction(self, query):
        r = _native_router()
        r._current_query_type = "diagnostic"
        cap = {}

        class _Resp:
            text = "..."
            quality_passed = True
            escalated = False
            local = True
            tokens_prompt = 0
            tokens_completion = 0

        def _chat(messages, **kw):
            cap["msgs"] = messages
            return _Resp()

        r._llm.chat = _chat
        r._screen_and_correct_claim = lambda text, *a, **k: text
        try:
            r._try_llm_freeform(query)
        except Exception:  # post-chat plumbing is irrelevant to the injected prompt
            pass
        instr = [m.content for m in cap.get("msgs", [])
                 if getattr(m, "role", None) == MessageRole.USER
                 and "IMPORTANT" in m.content]
        return instr[-1] if instr else ""

    def test_hedge_scoped_and_teaching_directed(self):
        inj = self._injected_instruction(
            "how do i make a password that's actually secure but that i can remember")
        self.assertTrue(inj, "the diagnostic freeform guard must be injected")
        # Scoped to live system state, no longer a blanket "no current data".
        self.assertIn("CURRENT STATE", inj)
        self.assertIn("general how-to", inj)
        self.assertIn("answer it directly", inj)
        # Anti-fabrication guard retained; the old unconditional hedge is gone.
        self.assertIn("Do not invent", inj)
        self.assertNotIn("If you cannot answer this from tool output", inj)


class CapabilityQuestionRouteTests(unittest.TestCase):
    """LEG 2: every capability QUESTION intercepts to capability_question BEFORE any
    dispatch (no 120 s wedge); real asks route on."""

    @classmethod
    def setUpClass(cls):
        cls.r = _native_router()

    def _src(self, q):
        return self.r.route(q, decide_only=True).source

    def test_sf_cap_tool_questions_intercept(self):
        for q in ("can you start and stop services?",
                  "would you mind are you able to restart a systemd service",
                  "can you open an app for me?",
                  "could you please are you able to launch programs?",
                  "can you read a file for me?",
                  "if you don't mind, are you able to open files?"):
            self.assertEqual(self._src(q), "capability_question", q)

    def test_web_and_pkm_capability_intercept_end_to_end(self):
        # The unification lifts the wave-3 web + wave-4 pkm intercepts out of the
        # normalization / explain shadow that silently defeated them end-to-end.
        for q in ("can you search the internet?",
                  "do you have internet access?",
                  "how do I use pkm add to manage packages?"):
            self.assertEqual(self._src(q), "capability_question", q)

    def test_real_asks_route_on(self):
        for q in ("read my notes file", "open firefox", "restart nginx",
                  "can you read /etc/hosts", "can you tell me a joke"):
            self.assertNotEqual(self._src(q), "capability_question", q)

    def test_identity_still_wins_for_what_can_you_do(self):
        self.assertEqual(self._src("what can you do"), "identity")


class ToolCapabilityGroundingTests(unittest.TestCase):
    """LEG 2 grounding: the yes/no is read from the live registry, and a mutating
    tool names the consent gate."""

    def _router_with(self, names):
        r = ConversationRouter.__new__(ConversationRouter)
        r._conversation_history = []
        r._append_history = lambda *a, **k: None
        r._record = lambda *a, **k: None
        # PRESENCE is controlled by `names` (so the tool-absent case is testable);
        # the consent-gated tail is grounded on the tool's REAL declared SafetyTier
        # via get_tool — the router derives the gate promise from the schema, never a
        # hardcoded flag, so the stub must serve the real tool for a faithful tier.
        _real = ToolRegistry()
        _real.discover_tools()
        r._tools = mock.Mock()
        r._tools.get_all_names.return_value = names
        r._tools.get_tool.side_effect = lambda n: _real.get_tool(n)
        return r

    def test_grounded_yes_names_consent_gate_for_mutating(self):
        r = self._router_with(["manage_services", "read_file"])
        res = r._try_tool_capability_question("can you start and stop services?", 0.0)
        self.assertEqual(res.source, "capability_question")
        self.assertIn("yes", res.text.lower())
        self.assertIn("confirmation", res.text.lower())  # gate named
        self.assertEqual(res.tool_calls, [])              # never dispatched

    def test_grounded_no_when_tool_absent(self):
        r = self._router_with(["read_file"])  # manage_services NOT present
        res = r._try_tool_capability_question("can you start and stop services?", 0.0)
        self.assertIsNotNone(res)
        self.assertIn("no", res.text.lower())
        self.assertIn("isn't available", res.text.lower())

    def test_readonly_yes_omits_gate_promise(self):
        r = self._router_with(["read_file"])
        res = r._try_tool_capability_question("can you read a file for me?", 0.0)
        self.assertIn("yes", res.text.lower())
        self.assertNotIn("confirmation", res.text.lower())

    def test_frame_precision(self):
        self.assertTrue(_CAP_Q_FRAME_RE.search("can you open an app"))
        self.assertTrue(_CAP_Q_FRAME_RE.search("are you able to read files"))
        self.assertFalse(_CAP_Q_FRAME_RE.search("open firefox now"))


class InlineDirLocationTailTests(unittest.TestCase):
    """RIDER: the inline-dir offer honors an explicit location tail; home is the
    default when none is named."""

    def _spec(self, text):
        return detect_file_lifecycle_intent(text, prior_draft=None, home=HOME)

    def test_named_home_relative_location(self):
        spec = self._spec("make a projects directory in my Downloads folder")
        self.assertEqual(spec["display"], f"mkdir -p {HOME}/Downloads/projects")
        self.assertEqual(spec["default_applied"], "~/Downloads")

    def test_explicit_paths(self):
        self.assertEqual(
            self._spec("make a data directory in ~/work")["display"],
            f"mkdir -p {HOME}/work/data")
        self.assertEqual(
            self._spec("make a logs directory in /srv/app")["display"],
            "mkdir -p /srv/app/logs")

    def test_home_default_preserved(self):
        for text in ("make a projects directory in my home folder",
                     "create a scripts folder"):
            spec = self._spec(text)
            self.assertTrue(spec["display"].startswith(f"mkdir -p {HOME}/"))
            self.assertNotIn("/Downloads/", spec["display"])
            self.assertEqual(spec["default_applied"], "home")


class GatingRegressionTests(unittest.TestCase):
    """Routing / prompt-assembly only — tool classification byte-identical."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def test_read_file_is_auto(self):
        self.assertEqual(
            self.registry.classify_safety("read_file", {"path": "/etc/hostname"}),
            SafetyTier.AUTO)

    def test_mutating_write_file_still_confirm(self):
        self.assertEqual(
            self.registry.classify_safety(
                "write_file", {"path": "/home/t/a.txt", "content": "x"}),
            SafetyTier.CONFIRM)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M8-1 eligibility redesign (wave 1) — trace-grounded RED/GREEN + gating proof.

The demand-corpus discovery run surfaced 605 HIGH `tool_starvation` findings
(consolidated ledger, runs smoke2/smoke4/merged1/full/policy-live): freeform
DO-asks reached the model with ZERO tool schemas, so a "save this file" /
"why won't my printer print" / "search the web for X" turn could only be
explained or fabricated. Root cause (M8 doc §3.1): the router eligibility gate
`(not _locked) and (score>=0.7 or diagnostic or safety)` implemented the trust
boundary as STARVATION.

M8-1 moves the trust boundary to the review gate: under the NATIVE (unlocked)
posture freeform/conversational turns GET the tool schemas; ToolRegistry.execute
still gates every mutating/privileged call fail-closed and dispatches read-only
(AUTO) tools under their existing gating. This test pins BOTH halves:

* SURFACE (RED->GREEN): representative starving turns lifted verbatim from the
  consolidated ledger are tool-STARVED under the LOCKED_DOWN floor (schemas
  absent — the pre-change behaviour, preserved byte-identical for the 2B) and
  tool-ELIGIBLE under NATIVE (schemas present — the fix). Each fixture scores
  low / classifies "general", so the OLD triple would have starved it under
  NATIVE too.
* GATING REGRESSION: the execute() boundary is byte-identical regardless of
  eligibility — a mutating (CONFIRM) call fails closed without a review UI and
  denies on a deny-choice; a read-only (AUTO) call dispatches. Widening
  eligibility widens SCHEMA EXPOSURE, never execution.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import intergen.trace as trace_mod
from intergen.interfaces.types import RouteResult, ToolCall
from intergen.interfaces.provenance import Provenance
from intergen.router import ConversationRouter
from intergen.tool_registry import ToolRegistry


# Representative starving turns, verbatim from the consolidated ledger
# (id, user, category, ebc). Each is a freeform DO/teach ask that the demand
# run recorded as `tool_starvation` (observed_source=llm_freeform, no dispatch).
LEDGER_STARVING_FIXTURES = [
    ("dd-device-0001", "why won't my printer print anything",
     "device_peripheral", "should_dispatch"),
    ("dd-device-0002", "my bluetooth headphones won't connect",
     "device_peripheral", "should_dispatch"),
    ("dd-do-0127", "make me 12 folders named january through december",
     "do_for_me", "should_gate"),
    ("dd-guide-0101",
     "whats a good way to back up my files so i dont lose everything if my "
     "laptop dies", "practical_guidance", "should_teach"),
    ("sf-offer-bare-yes-1-neutral", "install neovim for me",
     "do_for_me", "should_gate"),
]


def _records(state_dir: str) -> list[dict]:
    p = Path(state_dir) / "intergen" / "decisions.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line]


def _fallthrough_router(*, locked: bool, tools: ToolRegistry | None) -> ConversationRouter:
    """A bare router wired to fall through the fast paths to the P3 eligibility
    decision, with the dispatch lock and tool registry under test control."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._ingress_tracker = mock.Mock()
    r._metrics = None
    r._state_cache = None
    r._memory = None
    r._first_interaction = False
    r._hardware_tier = None
    r._lock_dispatch = locked
    r._tools = tools
    sem = mock.Mock()
    sem._normalize_input.side_effect = lambda x: x
    # Low semantic score + "general" query type => the OLD triple starves it.
    sem._match_embeddings.return_value = mock.Mock(
        score=0.12, intent_id=None, runner_up_score=0.0)
    r._semantic = sem
    return r


class M8EligibilitySurfaceTests(unittest.TestCase):
    """Trace-grounded RED/GREEN: ledger starving turns, floor vs NATIVE."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(setattr, trace_mod, "_tracer", None)
        # A real registry so tool_schemas_offered reflects the true offer set.
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

    def test_locked_floor_starves_every_ledger_fixture(self) -> None:
        """RED / the 2B floor, byte-identical to pre-change: no schemas."""
        for fid, user, cat, ebc in LEDGER_STARVING_FIXTURES:
            with self.subTest(id=fid, category=cat):
                router = _fallthrough_router(locked=True, tools=self.registry)
                attrs = self._route(router, user)
                self.assertTrue(attrs["dispatch_locked"], fid)
                self.assertFalse(attrs["eligible_for_tools"], fid)
                self.assertEqual(attrs["tool_schemas_offered"], [], fid)
                self.assertEqual(
                    attrs["eligibility_reason"], "locked_floor_code_owned", fid)

    def test_native_offers_schemas_to_every_ledger_fixture(self) -> None:
        """GREEN / the fix: NATIVE posture offers the schemas."""
        for fid, user, cat, ebc in LEDGER_STARVING_FIXTURES:
            with self.subTest(id=fid, category=cat):
                router = _fallthrough_router(locked=False, tools=self.registry)
                attrs = self._route(router, user)
                self.assertFalse(attrs["dispatch_locked"], fid)
                self.assertTrue(attrs["eligible_for_tools"], fid)
                self.assertEqual(
                    attrs["eligibility_reason"],
                    "native_freeform_schema_exposure", fid)
                offered = attrs["tool_schemas_offered"]
                self.assertTrue(offered, f"{fid}: expected a non-empty offer set")
                # web_search is the dominant starved category (128 findings) —
                # it must be on offer so a search-worthy freeform ask can reach
                # the read-only dispatch path.
                self.assertIn("web_search", offered, fid)

    def test_old_triple_would_have_starved_these_under_native(self) -> None:
        """The fixtures are genuine starvation cases: score 0.12 / "general"
        fail the retired (score>=0.7 or diagnostic or safety) gate, so the GREEN
        result is the redesign's doing, not an incidentally-high score."""
        router = _fallthrough_router(locked=False, tools=self.registry)
        attrs = self._route(router, LEDGER_STARVING_FIXTURES[0][1])
        old_triple = (attrs["eligibility_inputs"]["semantic_score"] >= 0.7
                      or attrs["query_type"] in ("diagnostic", "safety"))
        self.assertFalse(old_triple)
        self.assertTrue(attrs["eligible_for_tools"])


class M8GatingRegressionTests(unittest.TestCase):
    """The execute() trust boundary is byte-identical regardless of eligibility.

    These calls are constructed directly at the registry (not routed), so they
    hold whether or not the model was offered schemas — proving the redesign
    widens schema exposure, never execution.
    """

    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def _mutating_call(self) -> ToolCall:
        # manage_services restart => SafetyTier.CONFIRM (mutating).
        return ToolCall(
            name="manage_services",
            arguments={"action": "restart", "service": "sshd"},
            source_of_request=Provenance.USER_DIRECT,
        )

    def test_mutating_fails_closed_without_review_ui(self) -> None:
        """No review UI (callback=None) => implicit refusal, never a silent
        execute. This is the fail-closed boundary M8-1 leaves UNTOUCHED."""
        result = self.registry.execute(self._mutating_call(), review_callback=None)
        self.assertFalse(result.success)
        self.assertFalse(result.executed)

    def test_mutating_denied_on_deny_choice(self) -> None:
        """A mutating call ALWAYS reaches the review modal; a deny refuses."""
        seen = {}

        def _deny(call, decision):
            seen["asked"] = call.name
            return "deny"

        result = self.registry.execute(self._mutating_call(), review_callback=_deny)
        self.assertEqual(seen.get("asked"), "manage_services")
        self.assertFalse(result.success)
        self.assertFalse(result.executed)

    def test_readonly_dispatches_under_existing_gating(self) -> None:
        """A read-only (AUTO) call dispatches without a consent prompt — the
        'read-only classes keep their existing gating' half of the leg."""
        asked = {"n": 0}

        def _count(call, decision):
            asked["n"] += 1
            return "deny"

        call = ToolCall(
            name="read_file",
            arguments={"path": "/etc/hostname"},
            source_of_request=Provenance.USER_DIRECT,
        )
        result = self.registry.execute(call, review_callback=_count)
        # AUTO tier never routes to the review modal.
        self.assertEqual(asked["n"], 0)
        self.assertTrue(result.executed)


if __name__ == "__main__":
    unittest.main()

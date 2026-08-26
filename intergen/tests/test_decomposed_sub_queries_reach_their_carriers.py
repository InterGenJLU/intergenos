# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A decomposed clause must reach a carrier, and never dispatch a pronoun.

Two defects measured on a real re-drive, both on the compound path. ONE OF THEM IS
FIXED HERE AND ONE IS PINNED — read the split before reading the results:

  * Defect 2, the pronoun argument, is FIXED and asserted. Nine of these tests
    fail at base and pass on the branch.
  * Defect 1, the uncarried clause, is NOT fixed here. Its POLICY half is proven
    (the locked lane really does refuse the tools path) and its ROUTING half is
    recorded by a PIN that will fail the day it is fixed. Why it is not fixed is
    stated in full on that pin: the rung that would carry the clause is hardwired
    to run_command, and the rung that might already be claiming these clauses in
    production is the semantic one, which needs an embedding backend this context
    does not have. A carrier chosen without that measurement would be a guess.

1. A CLAUSE NO DETERMINISTIC CARRIER CLAIMS IS ANSWERED BY A MODEL TURN WITH NO
   TOOLS. `_handle_compound` (router.py) routes each clause through
   `_route_single`, whose ladder is keyword -> semantic -> a state-gated
   deterministic fallback -> `_try_llm_tools` -> `_try_llm_freeform`.
   `_try_llm_tools` returns handled=False AT ITS ENTRY whenever dispatch is
   locked, deliberately, so on a locked lane an action clause like "find a pdf
   editor" or "check if docker is installed" lands in `_try_llm_freeform`, whose
   messages are built with_tools=False. The model then invents system state or
   hedges about data it was never given a way to read, while the assistant HAS
   manage_packages and run_command. The decomposer's own contract says each part
   routes independently "so none is silently dropped"; an action clause answered
   by a no-tool model turn is a drop wearing an answer.

2. A CLAUSE WHOSE OBJECT IS A PRONOUN DISPATCHES THE PRONOUN. `_extract_arguments`
   takes the token after the verb, so "install it" dispatches
   manage_packages(action=install, package="it") and "restart the one that's
   stopped" dispatches manage_services(action=restart, service="the"). Measured
   against the real extractor, both exactly as the re-drive recorded them. The
   service branch even has a `_scan_service_name` fallback, and it never runs,
   because "the" is a non-empty string and the guard tests emptiness.

TIER SCOPE — the answer differs by tier for (1) and not at all for (2), and both
halves are derived from the product's own policy rather than assumed here.

  `lock_dispatch` is `dispatch_mode is LOCKED_DOWN` (dispatch_policy.py), and the
  daemon resolves the mode with `resolve_dispatch_for_model`, which grants NATIVE
  only when the resolved model's tier is in SHIPPED_LOGIC_LANES — a set holding
  TIER_2 alone. So the 2B floors LOCKED, the 9B runs NATIVE, and the 35B, having
  no shipped logic lane of its own, ALSO floors to LOCKED (fell_back_to_floor).
  The re-drive's 35B evidence is therefore a locked-floor box, not a large model
  hedging. These tests read that resolution rather than hardcoding a table, so a
  future shipped lane moves the expectation automatically.

  Defect (2) has no tier-conditional branch at all: `_extract_arguments` reads no
  tier, and `split_compound` returns the same clauses on all three (measured in
  `test_the_split_is_identical_on_every_tier`).

NAMED LIMIT: no embedding backend runs in this test context, so the SEMANTIC rung
of the ladder cannot be exercised and registration logs nine "Embedding intent
… PENDING" lines. That does not weaken these tests — the defect is precisely
about a clause that NO carrier claims, which is forced deterministically here —
but the semantic carrier's own behaviour is not measured by this file.

Model stubbed at the LLM boundary; no engine, no bus, no dispatch execution.
"""

from __future__ import annotations

import unittest

from intergen.decomposer import analyze_query, split_compound
from intergen.dispatch_policy import (
    SHIPPED_LOGIC_LANES,
    DispatchMode,
    resolve_dispatch_for_model,
)
from intergen.interfaces.types import HardwareTierLevel
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

# The daemon's three serving tiers, named as the battery names them.
TIERS = (
    ("2B", HardwareTierLevel.TIER_1),
    ("9B", HardwareTierLevel.TIER_2),
    ("35B", HardwareTierLevel.TIER_3),
)


def locked_for(tier: HardwareTierLevel) -> bool:
    """Whether the daemon would run this tier with dispatch LOCKED — asked of the
    product's own resolver, never tabulated here."""
    return resolve_dispatch_for_model(
        tier, detected_tier=tier).dispatch_mode is DispatchMode.LOCKED_DOWN


def _router(tier: HardwareTierLevel, *, replies=()) -> ConversationRouter:
    """A router built the way the daemon builds it — intents registered (they are
    consulted BEFORE the model sees a turn, and a router without them measures a
    different program) and the dispatch posture taken from the resolver for this
    tier rather than chosen by the test."""
    from intergen.intents import register_all_intents
    reg = ToolRegistry()
    reg.discover_tools()
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    r = ConversationRouter(
        tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
        lock_dispatch=locked_for(tier), hardware_tier=tier)
    seq = list(replies)
    calls = {"n": 0, "with_tools": []}

    def _chat(messages, **kw):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)] if seq else _Resp("")

    r._llm.chat = _chat
    r._chat_calls = calls
    return r


def _route_clause(r: ConversationRouter, clause: str):
    """Route one clause the way `_handle_compound` reaches `_route_single` — with
    the per-turn query type already classified.

    `_route_impl` sets `_current_query_type` before it routes anything
    (router.py:2055) and `_try_llm_freeform` reads it, so calling `_route_single`
    without it raises AttributeError. That is an instrument error, not a defect:
    a red that fires on a missing test-side attribute proves nothing about the
    product. Mirroring the real caller here is what makes the assertion below
    measure the routing decision instead of my own wiring.
    """
    r._current_query_type = r._classify_query_type(clause)
    r._route_trail = []
    return r._route_single(clause, trail_scope="sub_query:1")


class _Resp:
    """A completion carrying only what the freeform path reads."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.quality_passed = True
        self.escalated = False
        self.local = True
        self.model = "stub"
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.semantic_flags = []


# The two clauses the re-drive recorded reaching a no-tool model turn.
UNCARRIED_ACTION_CLAUSES = ("find a pdf editor", "check if docker is installed")


class TheTierPostureIsTheProductsOwn(unittest.TestCase):
    """The premise the per-tier table rests on, asserted rather than asserted-in-prose."""

    def test_only_tier_2_ships_a_native_logic_lane(self) -> None:
        self.assertEqual(SHIPPED_LOGIC_LANES, frozenset({HardwareTierLevel.TIER_2}))

    def test_the_floor_and_the_top_tier_both_run_locked(self) -> None:
        self.assertTrue(locked_for(HardwareTierLevel.TIER_1), "the 2B floor is locked")
        self.assertFalse(locked_for(HardwareTierLevel.TIER_2), "the 9B runs native")
        self.assertTrue(
            locked_for(HardwareTierLevel.TIER_3),
            "the 35B has no shipped logic lane, so the daemon floors it to LOCKED — "
            "which is why the re-drive's 35B answers were no-tool model turns")

    def test_the_top_tier_floors_rather_than_walking_down(self) -> None:
        res = resolve_dispatch_for_model(HardwareTierLevel.TIER_3,
                                         detected_tier=HardwareTierLevel.TIER_3)
        self.assertTrue(res.fell_back_to_floor)
        self.assertEqual(res.tier, HardwareTierLevel.TIER_1)


class AnUncarriedActionClauseIsNotAnsweredWithoutTools(unittest.TestCase):
    """Defect 1. RED at base on every tier that runs locked."""

    def test_no_carrier_claims_these_clauses(self) -> None:
        """The premise: these really are clauses no deterministic rung claims, so
        the ladder's last rung is what answers them."""
        for name, tier in TIERS:
            r = _router(tier)
            for clause in UNCARRIED_ACTION_CLAUSES:
                with self.subTest(tier=name, clause=clause):
                    self.assertFalse(r._try_keyword_match(clause).handled)
                    self.assertFalse(r._try_deterministic_fallback(clause).handled)

    def test_an_action_clause_falls_to_the_no_tool_model_turn_TODAY(self) -> None:
        """PIN, NOT A FIX — this records the defect exactly as it stands, so it is
        visible in the tree instead of being re-discovered at the next re-drive,
        and so nobody reads this file as proof that defect 1 was closed.

        WHY IT IS A PIN. The routing half is NOT fixed in this lane, and the
        reason is a measurement this box cannot make. `_try_deterministic_fallback`
        — the rung that would carry an uncarried clause — is hardwired to
        `run_command` (router.py, `_execute_tool_for_intent("run_command", ...)`),
        so it is the read-only state fast path, not a general carrier: it cannot
        route "find a pdf editor" to manage_packages. Selecting the right carrier
        for an ACTION clause needs either a new deterministic selector or the
        SEMANTIC rung, and the semantic rung needs an embedding backend that does
        not run in this context. Until that is measured against a live embedder,
        whether these clauses reach freeform IN PRODUCTION is unknown: the daemon
        has an embedder and its semantic rung may well claim them. Guessing a
        carrier here would be a speculative fix to a defect whose reproduction is
        unconfirmed on the machine that matters.

        WHAT IS PROVEN. The POLICY half, in `TheTierPostureIsTheProductsOwn`: on
        the 2B and the 35B the lane is locked, so `_try_llm_tools` refuses at its
        entry and the tools path is genuinely unavailable to these clauses. That
        is the mechanism the re-drive's answers are consistent with.

        This test FAILS the day the routing is fixed, which is the point: it will
        say so rather than silently keep passing."""
        for name, tier in TIERS:
            if not locked_for(tier):
                continue
            for clause in UNCARRIED_ACTION_CLAUSES:
                with self.subTest(tier=name, clause=clause):
                    r = _router(tier, replies=[_Resp("qpdf is available.")])
                    result = _route_clause(r, clause)
                    self.assertEqual(
                        result.source, "llm_freeform",
                        f"[{name}] {clause!r} no longer falls to the no-tool model "
                        f"turn — the routing half has been fixed and this PIN "
                        f"should become the assertion it was written from")

    def test_the_native_tier_has_a_tools_path_at_all(self) -> None:
        """The 9B's half of the per-tier answer: its clause is not refused at the
        `_try_llm_tools` entry gate, because that lane is not locked. This is the
        policy fact, not a claim about what the model chooses."""
        self.assertFalse(locked_for(HardwareTierLevel.TIER_2))
        r = _router(HardwareTierLevel.TIER_2)
        self.assertFalse(r._lock_dispatch,
                         "the native lane must not refuse the tools path at entry")

    def test_a_knowledge_clause_still_reaches_the_model(self) -> None:
        """CONTROL. The fix must not drag a pure-knowledge clause onto an action
        path — "what is a pdf" has nothing to dispatch and the model is right."""
        for name, tier in TIERS:
            with self.subTest(tier=name):
                r = _router(tier, replies=[_Resp("A PDF is a document format.")])
                result = _route_clause(r, "what is a pdf")
                self.assertTrue(result.handled)


class APronounIsNeverDispatchedAsAnArgument(unittest.TestCase):
    """Defect 2. RED at base; measured against the real extractor."""

    def test_install_it_does_not_dispatch_the_pronoun(self) -> None:
        for name, tier in TIERS:
            with self.subTest(tier=name):
                args = _router(tier)._extract_arguments("manage_packages", "install it")
                if args is not None:
                    self.assertNotEqual(
                        args.get("package"), "it",
                        f"[{name}] the pronoun was dispatched as the package name")

    def test_restart_the_one_does_not_dispatch_a_determiner(self) -> None:
        for name, tier in TIERS:
            with self.subTest(tier=name):
                args = _router(tier)._extract_arguments(
                    "manage_services", "restart the one that's stopped")
                if args is not None:
                    self.assertNotEqual(
                        args.get("service"), "the",
                        f"[{name}] a determiner was dispatched as the service name")

    def test_a_real_object_still_dispatches_unchanged(self) -> None:
        """CONTROL, and the thing a careless guard would break."""
        for name, tier in TIERS:
            with self.subTest(tier=name):
                r = _router(tier)
                self.assertEqual(
                    r._extract_arguments("manage_packages", "install firefox"),
                    {"action": "install", "package": "firefox"})
                self.assertEqual(
                    r._extract_arguments("manage_services", "restart sshd"),
                    {"action": "restart", "service": "sshd"})

    def test_the_service_scan_is_reached_when_the_name_is_elsewhere(self) -> None:
        """The existing `_scan_service_name` fallback exists for exactly this and
        was unreachable, because a determiner is a non-empty string."""
        for name, tier in TIERS:
            with self.subTest(tier=name):
                args = _router(tier)._extract_arguments(
                    "manage_services", "restart the sshd service")
                self.assertIsNotNone(args)
                self.assertEqual(args.get("service"), "sshd")


class TheDecompositionCarriesItsReferent(unittest.TestCase):
    """Defect 2's compound half: the clause that names the object comes first."""

    def test_the_split_is_identical_on_every_tier(self) -> None:
        """No tier-conditional branch in the splitter — asserted, not assumed."""
        for name, tier in TIERS:
            with self.subTest(tier=name):
                d = analyze_query("find a pdf editor and install it", tier)
                self.assertTrue(d.is_compound)
                self.assertEqual(d.sub_queries, ["find a pdf editor", "install it"])

    def test_the_referent_clause_precedes_the_pronoun_clause(self) -> None:
        self.assertEqual(split_compound("find a pdf editor and install it"),
                         ["find a pdf editor", "install it"])

    def test_the_single_clause_case_is_not_a_split_at_all(self) -> None:
        """Correcting the record: "restart the one that's stopped" carries no
        conjunction, so its bad argument is the extractor's alone and no amount of
        referent-carrying in the decomposer would have reached it."""
        self.assertEqual(split_compound("restart the one that's stopped"),
                         ["restart the one that's stopped"])


if __name__ == "__main__":
    unittest.main()

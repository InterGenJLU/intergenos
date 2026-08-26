# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The phone-a-friend offer fires selectively, not on every conversational turn.

THE DEFECT, read before written. escalation._LOW_CONFIDENCE is 3.0 and carries the
comment "Local self-rated confidence (1-5)". The abstract contract says the same
(interfaces/cloud.py, should_escalate: "confidence: Local model's self-rated
confidence (1-5, <3 triggers)"). No live caller works on that scale. The one
producer is ConversationRouter._try_llm_freeform:

    confidence = 1.0 if response.quality_passed else 0.5

which it hands to _maybe_offer and on to should_escalate. Both live values are at
or below 3.0, so

    low_confidence = bool(confidence) and confidence <= _LOW_CONFIDENCE

is TRUE on every freeform turn and the offer fires on all of them. A threshold that
can never be false is a check that does not check: the signal is present in the
decision, contributes nothing, and hides the fact that it contributes nothing.

Two consequences are pinned here, not one:
  * a confident turn whose answer passed the quality gate still earns an offer;
  * a confidence of exactly 0.0 — the LEAST confident value the 0-1 scale has —
    reads as NOT low confidence, because `bool(0.0)` is False. The guard that was
    meant to skip an absent value silences the extreme it most needs to catch.

TIER SCOPE (the 2026-08-26 amendment). The defect is TIER-INDEPENDENT. The only
tier input on this path is ConversationRouter._hardware_tier, passed to
decomposer.analyze_query inside _maybe_offer; decomposer._TIER_THRESHOLDS is
diagnostic-only and gates no decision (stated in analyze_query and in that module's
docstring), and needs_decomposition is computed from clause count and content words
alone. EscalationManager.should_escalate takes no tier parameter at all. The
router-level tests below run under all three serving tiers to prove by measurement
what the reading says.

Model stubbed at the LLM boundary — no engine, no dispatch, seconds to run.
"""

from __future__ import annotations

import unittest

from intergen import escalation as escalation_mod
from intergen.escalation import EscalationManager
from intergen.interfaces.cloud import ProviderConfig
from intergen.interfaces.types import EscalationMode, HardwareTierLevel
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

# The two values the ONE live producer can pass, quoted from
# ConversationRouter._try_llm_freeform: `confidence = 1.0 if quality_passed else 0.5`.
LIVE_CONFIDENT = 1.0
LIVE_UNSURE = 0.5

# A single-clause, non-compound ask that carries no explicit request for the
# frontier model, so the ONLY trigger it could fire is low_confidence.
PLAIN = "what is a semaphore"


class _Resp:
    """A completion result carrying only what the freeform path reads."""

    def __init__(self, text, *, quality_passed=True, semantic_flags=None):
        self.text = text
        self.quality_passed = quality_passed
        self.escalated = False
        self.local = True
        self.model = "stub"
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.semantic_flags = list(semantic_flags or [])


def _cfg(name="acme", adapter="openai"):
    return ProviderConfig(name=name, adapter=adapter, model="acme-1",
                          api_key_keyring_id="intergen-acme")


def _mgr(providers=None):
    """The manager in its default ASK mode. A provider is configured so the
    decision under test is the TRIGGER SET, not the no-provider setup-pointer
    branch, which has a trigger path of its own."""
    return EscalationManager(mode=EscalationMode.ASK,
                             providers=[_cfg()] if providers is None else providers,
                             adapter_factory=lambda c: object())


def _router(tier: HardwareTierLevel, reply) -> ConversationRouter:
    reg = ToolRegistry()
    reg.discover_tools()
    r = ConversationRouter(
        tool_registry=reg, semantic_matcher=SemanticMatcher(embedder=None),
        llm=LLMRouter(config=None), lock_dispatch=False, hardware_tier=tier)
    r._escalation = _mgr()
    r._llm.chat = lambda messages, **kw: reply
    return r


class ThresholdIsOnTheScaleTheLiveCallerUsesTests(unittest.TestCase):
    """The scale contract itself. These fail at the base because 3.0 sits above
    every value the live producer can pass, which is what makes the check inert."""

    def test_the_threshold_is_below_the_confident_value(self):
        self.assertLess(
            escalation_mod._LOW_CONFIDENCE, LIVE_CONFIDENT,
            "a threshold at or above the most confident value the live caller "
            "passes can never be false — the check does not check")

    def test_the_threshold_is_at_or_above_the_unsure_value(self):
        self.assertGreaterEqual(
            escalation_mod._LOW_CONFIDENCE, LIVE_UNSURE,
            "the live caller's unsure value must still read as low confidence")


class ConfidenceIsReadOnTheLiveScaleTests(unittest.TestCase):
    """should_escalate on its own, with every other trigger held quiet."""

    def test_a_confident_quality_passed_turn_is_not_low_confidence(self):
        d = _mgr().should_escalate(PLAIN, "an answer", "", LIVE_CONFIDENT)
        self.assertFalse(
            d.should_escalate,
            "a confident turn whose answer passed the quality gate earned an "
            f"offer anyway: {d.reason!r}")

    def test_an_unsure_turn_is_still_low_confidence(self):
        d = _mgr().should_escalate(PLAIN, "an answer", "", LIVE_UNSURE)
        self.assertTrue(d.should_escalate)
        self.assertEqual(d.reason, "I am not confident in my local answer")

    def test_zero_confidence_is_the_least_confident_value_not_an_absent_one(self):
        # `bool(confidence)` made 0.0 — the floor of the 0-1 scale — read as
        # NOT low confidence, silencing the extreme case.
        d = _mgr().should_escalate(PLAIN, "an answer", "", 0.0)
        self.assertTrue(
            d.should_escalate,
            "a confidence of 0.0 is the least confident value on this scale and "
            "must read as low confidence")

    def test_a_quality_failure_still_offers_at_full_confidence(self):
        # The guard: correcting the scale must not disarm the other triggers.
        d = _mgr().should_escalate(PLAIN, "", "local quality gate failed",
                                   LIVE_CONFIDENT)
        self.assertTrue(d.should_escalate)
        self.assertEqual(d.reason, "my local answer did not pass the quality gate")

    def test_an_explicit_ask_still_offers_at_full_confidence(self):
        d = _mgr().should_escalate("ask my frontier model", "an answer", "",
                                   LIVE_CONFIDENT)
        self.assertTrue(d.should_escalate)
        self.assertEqual(d.reason, "you asked me to reach your frontier model")


class ConfidentFreeformTurnEarnsNoOfferTests(unittest.TestCase):
    """End to end through the router, under each serving tier."""

    def test_a_confident_freeform_turn_carries_no_offer(self):
        for name, tier in TIERS:
            with self.subTest(tier=name):
                r = _router(tier, _Resp("A semaphore is a counter guarding a "
                                        "shared resource."))
                r._current_query_type = "general"
                result = r._try_llm_freeform(PLAIN)
                self.assertIsNone(
                    result.escalation_offer,
                    "a confident, quality-passed conversational turn earned the "
                    f"phone-a-friend offer: {result.escalation_offer!r}")

    def test_a_quality_failed_freeform_turn_still_carries_the_offer(self):
        # The guard at router level: the offer must keep firing where it should.
        for name, tier in TIERS:
            with self.subTest(tier=name):
                r = _router(tier, _Resp("...", quality_passed=False))
                r._current_query_type = "general"
                result = r._try_llm_freeform(PLAIN)
                self.assertIsNotNone(
                    result.escalation_offer,
                    "a turn whose answer failed the quality gate lost its offer")


class TheDocumentedScaleMatchesTheCodeTests(unittest.TestCase):
    """The comment and the abstract contract are part of the defect: both said
    1-5 while the only producer passed 0-1. A corrected threshold with a stale
    comment beside it sets the same trap for whoever reads the code afterwards."""

    def test_the_threshold_comment_does_not_claim_the_one_to_five_scale(self):
        import inspect
        src = inspect.getsource(escalation_mod).splitlines()
        idx = [i for i, l in enumerate(src) if "_LOW_CONFIDENCE =" in l][0]
        comment = "\n".join(src[max(0, idx - 6):idx])
        self.assertNotIn("(1-5)", comment,
                         "the threshold's own comment still states the 1-5 scale")

    def test_the_abstract_contract_does_not_claim_the_one_to_five_scale(self):
        import inspect
        from intergen.interfaces import cloud
        doc = inspect.getsource(cloud)
        self.assertNotIn(
            "(1-5, <3 triggers)", doc,
            "interfaces/cloud.py still documents should_escalate's confidence "
            "on the 1-5 scale no caller uses")


if __name__ == "__main__":
    unittest.main()

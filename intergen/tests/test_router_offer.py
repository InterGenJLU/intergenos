# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Phone-a-friend router OFFER (decision #4 heuristic half) — design plan §4.

Tests ConversationRouter._maybe_offer in isolation (no heavy router construction):
should_escalate=True on a local answer yields an offer string; an already-escalated
(cloud) answer offers nothing; no manager / no provider / a manager exception all
yield None; the offer NEVER sends (it is advisory only). Runs on any host.
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter
from intergen.interfaces.cloud import EscalationDecision
from intergen.interfaces.types import LLMResponse


class _FakeEscalation:
    def __init__(self, decision):
        self._decision = decision
        self.escalate_called = False
        self.multistep_seen: bool | None = None
        self.exceeds_scope_seen: bool | None = None

    def should_escalate(self, user_message, local_response, quality_check,
                        confidence, *, multistep=False, exceeds_scope=False):
        # Mirrors EscalationManagerInterface.should_escalate exactly. A stand-in
        # that omits a keyword the router passes raises TypeError, which the
        # offer path swallows by design — so the offer stops appearing instead of
        # failing loudly, and the test reads as a product regression.
        self.multistep_seen = multistep
        self.exceeds_scope_seen = exceeds_scope
        return self._decision

    def escalate(self, *a, **k):  # must NEVER be called by the offer path
        self.escalate_called = True
        raise AssertionError("the OFFER must not send")


def _router(escalation):
    r = ConversationRouter.__new__(ConversationRouter)
    r._escalation = escalation
    return r


def _local(text="local answer", quality_passed=True):
    return LLMResponse(text=text, model="local", local=True,
                       quality_passed=quality_passed)


class MaybeOfferTests(unittest.TestCase):
    def test_offer_when_should_escalate(self):
        esc = _FakeEscalation(
            EscalationDecision(True, "this looks multi-step / outside my local scope",
                               0.7, "anthropic"))
        r = _router(esc)
        offer = r._maybe_offer("do a then b then c", _local(), 1.0)
        self.assertIsNotNone(offer)
        self.assertIn("anthropic", offer)
        self.assertFalse(esc.escalate_called)  # advisory only — nothing sent

    def test_offer_voice_is_the_ruled_verbatim_text(self):
        # Decided 2026-07-23: the provider-present offer text is VERBATIM — pin
        # its load-bearing phrases so a re-word cannot land without a decision.
        esc = _FakeEscalation(
            EscalationDecision(True, "I am not confident in my local answer",
                               0.7, "anthropic"))
        r = _router(esc)
        offer = r._maybe_offer("q", _local(), 1.0)
        self.assertIn("reach out to your designated frontier model (anthropic)", offer)
        self.assertIn("if you'd like me to- I am not confident", offer)
        self.assertIn("type 'ask my frontier model' in chat", offer)
        self.assertIn("it looks like a phone", offer)
        self.assertIn("review what's sent before it goes", offer)

    def test_multistep_signal_is_the_decomposer_verdict(self):
        # The offer path feeds should_escalate the decomposer's structured
        # multi-part verdict for the same input — never a parallel text regex.
        from intergen.decomposer import analyze_query
        from intergen.interfaces.types import HardwareTierLevel
        esc = _FakeEscalation(
            EscalationDecision(False, "local response is sufficient", 0.0, None))
        r = _router(esc)
        for text in ("what time is it",
                     "check my disk usage and then update my packages"):
            r._maybe_offer(text, _local(), 1.0)
            expected = analyze_query(text, HardwareTierLevel.TIER_2)
            self.assertEqual(esc.multistep_seen, expected.needs_decomposition,
                             f"multistep verdict mismatch for {text!r}")

    def test_no_offer_when_not_recommended(self):
        esc = _FakeEscalation(
            EscalationDecision(False, "local response is sufficient", 0.0, None))
        r = _router(esc)
        self.assertIsNone(r._maybe_offer("what time is it", _local(), 1.0))

    def test_no_offer_when_already_cloud(self):
        # A cloud answer (FALLBACK auto-escalation) has nothing to offer.
        esc = _FakeEscalation(
            EscalationDecision(True, "x", 0.7, "anthropic"))
        r = _router(esc)
        cloud = LLMResponse(text="cloud answer", model="anthropic-1", local=False)
        self.assertIsNone(r._maybe_offer("q", cloud, 1.0))

    def test_no_manager_no_offer(self):
        r = _router(None)
        self.assertIsNone(r._maybe_offer("q", _local(), 1.0))

    def test_no_provider_offers_setup_path(self):
        # Decided 2026-07-23: signals fire + NO designated provider → the offer
        # becomes the wiki-cited provider-setup pointer, not silence.
        esc = _FakeEscalation(EscalationDecision(True, "x", 0.7, None))
        r = _router(esc)
        offer = r._maybe_offer("q", _local(), 1.0)
        self.assertIsNotNone(offer)
        self.assertIn("haven't designated a frontier model provider yet", offer)
        self.assertIn("as described in the Wiki", offer)
        # No WikiCitations on this bare router → graceful degrade: bare text,
        # no citation line.
        self.assertNotIn("Source:", offer)
        self.assertFalse(esc.escalate_called)

    def test_manager_exception_degrades_to_none(self):
        class _Boom:
            def should_escalate(self, *a, **k):
                raise RuntimeError("boom")
        r = _router(_Boom())
        self.assertIsNone(r._maybe_offer("q", _local(), 1.0))


if __name__ == "__main__":
    unittest.main()

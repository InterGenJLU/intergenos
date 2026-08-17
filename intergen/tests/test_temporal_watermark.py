# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Temporal-watermark fix (audit 2026-05-29) — cross-turn ingress escalation.

The provenance gate was inert against fetch-poison-then-act-NEXT-turn: the
IngressTracker was recreated every turn, so an ingress fire in turn T was
invisible when a privileged call in turn T+1 declared USER_DIRECT — the watermark
never tripped and the call executed. These tests prove the per-conversation
window now persists across turns and escalates a later privileged call's
effective provenance from USER_DIRECT to USER_IMPLIED, flipping the gate from
execute to hold_for_review.
"""

from __future__ import annotations

import unittest

from intergen.interfaces.provenance import (
    IngressTracker,
    ConversationTrustState,
    Provenance,
    ToolRiskTier,
    escalate_provenance,
)
from intergen.interfaces.types import ToolCall
from intergen.provenance import verify_tool_call


class TrackerWindowTests(unittest.TestCase):
    def test_record_populates_both_windows(self):
        t = IngressTracker()
        t.record_tool_call("web_search")
        self.assertTrue(t.ingress_fired_this_turn())
        self.assertTrue(t.ingress_fired_this_conversation())

    def test_per_turn_reset_preserves_conversation_window(self):
        t = IngressTracker()
        t.record_tool_call("web_search")
        t.reset()  # turn boundary
        self.assertFalse(t.ingress_fired_this_turn())
        self.assertTrue(t.ingress_fired_this_conversation())  # the whole fix
        self.assertEqual(t.conversation_history(), ["web_search"])

    def test_conversation_reset_clears_both(self):
        t = IngressTracker()
        t.record_tool_call("read_file")
        t.reset_conversation()
        self.assertFalse(t.ingress_fired_this_turn())
        self.assertFalse(t.ingress_fired_this_conversation())

    def test_non_ingress_tool_not_recorded(self):
        t = IngressTracker()
        t.record_tool_call("manage_services")  # privileged, not ingress
        self.assertFalse(t.ingress_fired_this_turn())
        self.assertFalse(t.ingress_fired_this_conversation())


class EscalationTableTests(unittest.TestCase):
    def test_no_ingress_no_escalation(self):
        self.assertEqual(
            escalate_provenance(Provenance.USER_DIRECT, False, False),
            Provenance.USER_DIRECT,
        )

    def test_conversation_only_ingress_escalates(self):
        # The cross-turn case: nothing fired THIS turn, but ingress fired earlier
        # in the conversation -> one-step escalation.
        self.assertEqual(
            escalate_provenance(Provenance.USER_DIRECT, False, True),
            Provenance.USER_IMPLIED,
        )

    def test_same_turn_ingress_still_escalates(self):
        self.assertEqual(
            escalate_provenance(Provenance.USER_DIRECT, True, False),
            Provenance.USER_IMPLIED,
        )

    def test_user_implied_plus_conversation_ingress_goes_top(self):
        self.assertEqual(
            escalate_provenance(Provenance.USER_IMPLIED, False, True),
            Provenance.INGRESS_DERIVED,
        )

    def test_ingress_derived_stays_top(self):
        self.assertEqual(
            escalate_provenance(Provenance.INGRESS_DERIVED, False, False),
            Provenance.INGRESS_DERIVED,
        )


class CrossTurnGateTests(unittest.TestCase):
    """End-to-end: the gate decision for a privileged USER_DIRECT call changes
    from execute to hold once prior-turn ingress is preserved."""

    def _privileged_call(self):
        return ToolCall(
            name="manage_services",
            arguments={"action": "restart", "unit": "sshd"},
            source_of_request=Provenance.USER_DIRECT,
        )

    def test_no_prior_ingress_executes(self):
        # Baseline: a fresh conversation, privileged USER_DIRECT -> execute.
        tracker = IngressTracker()
        decision = verify_tool_call(
            self._privileged_call(), tracker, ConversationTrustState(),
            ToolRiskTier.PRIVILEGED_STATE_CHANGING,
        )
        self.assertEqual(decision.action, "execute")
        self.assertEqual(decision.effective_provenance, Provenance.USER_DIRECT)

    def test_prior_turn_ingress_holds(self):
        # Turn 1: an ingress tool fired. Turn boundary: per-turn reset.
        tracker = IngressTracker()
        tracker.record_tool_call("web_search")
        tracker.reset()  # turn 2 begins; same-turn window cleared
        # Turn 2: the SAME privileged USER_DIRECT call now escalates to
        # USER_IMPLIED via the preserved conversation window -> hold.
        decision = verify_tool_call(
            self._privileged_call(), tracker, ConversationTrustState(),
            ToolRiskTier.PRIVILEGED_STATE_CHANGING,
        )
        self.assertEqual(decision.action, "hold_for_review")
        self.assertEqual(decision.effective_provenance, Provenance.USER_IMPLIED)

    def test_user_scope_not_held_by_one_step(self):
        # One-step escalation keeps USER_SCOPE executing (the low-friction
        # property): USER_DIRECT -> USER_IMPLIED, and (USER_SCOPE, USER_IMPLIED)
        # = execute.
        call = ToolCall(
            name="open_application",
            arguments={"app": "gnome-calculator"},
            source_of_request=Provenance.USER_DIRECT,
        )
        tracker = IngressTracker()
        tracker.record_tool_call("web_search")
        tracker.reset()
        decision = verify_tool_call(
            call, tracker, ConversationTrustState(),
            ToolRiskTier.USER_SCOPE_STATE_CHANGING,
        )
        self.assertEqual(decision.action, "execute")
        self.assertEqual(decision.effective_provenance, Provenance.USER_IMPLIED)


if __name__ == "__main__":
    unittest.main()

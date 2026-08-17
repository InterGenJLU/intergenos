# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""PI-Z29 (r27) preventive-grounding window — regression pins.

After InterGen OFFERS to run a command, a short decaying window injects a factual
"nothing has run yet" note so the small model cannot narrate a fabricated
execution off the offer text still in history. PI-Z29 fixed the OVER-STEER: the
note was injected on EVERY turn while the window was open, so an unrelated turn
("did the stock market close today?") had its answer stolen by the note. The fix
injects only when THIS turn plausibly relates to the live offer.

Pinned here:
  * _turn_relates_to_offer — affirmative OR offer-term overlap → True; unrelated
    → False; with no live offer, a bare affirmative still relates, off-topic does not.
  * offer-stage arms the window (TTL = _OFFER_GROUNDING_TTL = 4) and captures the
    offered command's content terms.
  * _build_messages injection gate — window-open + related → inject + glass
    'injected'; window-open + unrelated → NO note + glass 'skipped_unrelated';
    toolless path only (a with_tools turn never injects); closed window → nothing.
  * reset_conversation_state / the daemon's ResetConversation clear the window
    (TTL + terms) alongside history + offers.

NB: the per-turn TTL DECREMENT and the 'window_expired' glass row on decay live
inline at the top of ConversationRouter._route_impl (intergen/router.py, the
'age the window by one turn' block) — no unit seam; that transition is exercised
end-to-end by the honesty battery through the real route path. This fixture pins
every isolatable end of the same lifecycle.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import intergen.glass as glass
from intergen.router import ConversationRouter


OFFER_TERMS = frozenset({"pkm", "sync", "upgrade"})


def _glass_reset(tmp: str) -> None:
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _glass_rows(tmp: str) -> list[dict]:
    p = Path(tmp) / "intergen" / "glass.jsonl"
    if not p.exists():
        return []
    with open(p) as f:
        return [json.loads(x) for x in f]


class TurnRelatesToOffer(unittest.TestCase):
    def setUp(self) -> None:
        self.r = ConversationRouter.__new__(ConversationRouter)
        self.r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        self.r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        self.r._offer_topic_terms = OFFER_TERMS

    def test_affirmatives_relate(self) -> None:
        for turn in ("yes", "go ahead", "sure, do it"):
            self.assertTrue(self.r._turn_relates_to_offer(turn), turn)

    def test_offer_term_overlap_relates(self) -> None:
        for turn in ("did the upgrade finish", "is pkm done"):
            self.assertTrue(self.r._turn_relates_to_offer(turn), turn)

    def test_unrelated_turns_do_not_relate(self) -> None:
        for turn in ("did the stock market close today",
                     "what's the weather", "tell me a joke"):
            self.assertFalse(self.r._turn_relates_to_offer(turn), turn)

    def test_no_live_offer_only_affirmatives_relate(self) -> None:
        self.r._offer_topic_terms = frozenset()
        self.assertTrue(self.r._turn_relates_to_offer("yes"))
        self.assertFalse(self.r._turn_relates_to_offer("stocks closed?"))


class OfferStageArmsWindow(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _glass_reset(self.tmp)

    def test_ttl_constant_is_four(self) -> None:
        self.assertEqual(ConversationRouter._OFFER_GROUNDING_TTL, 4)

    def test_staging_an_action_arms_ttl_and_captures_terms(self) -> None:
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        with glass.turn(glass.new_turn_id(), "test"):
            r._stage_single_offer(action=("pkm upgrade", "run_command", "q"))
        self.assertEqual(r._action_offer_ttl, 4)
        self.assertEqual(r._offer_topic_terms, frozenset({"pkm", "upgrade"}))
        stage = [x for x in _glass_rows(self.tmp) if x.get("event") == "offer_stage"]
        self.assertTrue(stage)
        self.assertEqual(stage[-1]["detail"]["slot"], "action")
        self.assertEqual(stage[-1]["detail"]["command"], "pkm upgrade")


class _FakeLLM:
    def build_system_messages(self, query_type="general", with_tools=True):
        return []


class BuildMessagesInjectionGate(unittest.TestCase):
    """The over-steer fix: the no-dispatch note injects ONLY on a related turn
    while the window is open, and only on the toolless path."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _glass_reset(self.tmp)
        self.r = ConversationRouter.__new__(ConversationRouter)
        self.r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        self.r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        self.r._llm = _FakeLLM()
        self.r._conversation_history = []
        self.r._max_history = 20
        self.r._current_query_type = "general"
        self.r._offer_topic_terms = OFFER_TERMS
        self.r._offer_in_recent_history = True  # window OPEN

    def _preventive_rows(self) -> list[dict]:
        return [x for x in _glass_rows(self.tmp)
                if x.get("event") == "preventive_grounding"]

    def _has_note(self, msgs) -> bool:
        note = ConversationRouter._PREVENTIVE_GROUNDING_NOTE
        return any(getattr(m, "content", None) == note for m in msgs)

    def test_open_window_related_turn_injects(self) -> None:
        with glass.turn(glass.new_turn_id(), "test"):
            msgs = self.r._build_messages("did the upgrade finish", with_tools=False)
        self.assertTrue(self._has_note(msgs))
        rows = self._preventive_rows()
        self.assertEqual(rows[-1]["detail"]["decision"], "injected")

    def test_open_window_unrelated_turn_is_skipped(self) -> None:
        with glass.turn(glass.new_turn_id(), "test"):
            msgs = self.r._build_messages("did the stock market close today",
                                          with_tools=False)
        self.assertFalse(self._has_note(msgs))
        rows = self._preventive_rows()
        self.assertEqual(rows[-1]["detail"]["decision"], "skipped_unrelated")

    def test_with_tools_turn_never_injects(self) -> None:
        # Scoped to the toolless path — a with_tools turn can genuinely dispatch.
        with glass.turn(glass.new_turn_id(), "test"):
            msgs = self.r._build_messages("did the upgrade finish", with_tools=True)
        self.assertFalse(self._has_note(msgs))
        decisions = [r["detail"]["decision"] for r in self._preventive_rows()]
        self.assertNotIn("injected", decisions)
        self.assertNotIn("skipped_unrelated", decisions)

    def test_closed_window_never_injects(self) -> None:
        self.r._offer_in_recent_history = False  # window CLOSED
        with glass.turn(glass.new_turn_id(), "test"):
            msgs = self.r._build_messages("did the upgrade finish", with_tools=False)
        self.assertFalse(self._has_note(msgs))
        self.assertEqual(self._preventive_rows(), [])


class ResetClearsWindow(unittest.TestCase):
    """reset_conversation_state clears the window (TTL + terms) with everything
    else — the per-conversation isolation ResetConversation delivers over D-Bus."""

    def test_reset_clears_ttl_and_terms(self) -> None:
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        r._pending_action_offer = ("pkm upgrade", "run_command", "q")
        r._pending_ipv6_offer = None
        r._pending_memory_offer = None
        r._trust_state = None

        class _Tracker:
            def reset_conversation(self):
                pass

        r._ingress_tracker = _Tracker()
        r._conversation_history = ["t1"]
        r._first_interaction = False
        r._action_offer_ttl = 4
        r._offer_in_recent_history = True
        r._offer_topic_terms = OFFER_TERMS

        r.reset_conversation_state()

        self.assertEqual(r._action_offer_ttl, 0)
        self.assertFalse(r._offer_in_recent_history)
        self.assertEqual(r._offer_topic_terms, frozenset())
        self.assertIsNone(r._pending_action_offer)


class DaemonResetConversationMethod(unittest.TestCase):
    """The D-Bus ResetConversation() method: {"reset": true} on success, and the
    fail-loud {"reset": false, "reason": "router not started"} when the router is
    down — the contract the harness treats as a hard error."""

    def _daemon(self, router):
        from intergen.dbus_daemon import InterGenDaemon
        d = InterGenDaemon.__new__(InterGenDaemon)
        d._router = router
        return d

    def test_reset_true_delegates_to_router(self) -> None:
        class _Router:
            def __init__(self):
                self.calls = 0

            def reset_conversation_state(self):
                self.calls += 1

        router = _Router()
        d = self._daemon(router)
        self.assertEqual(json.loads(d.reset_conversation()), {"reset": True})
        self.assertEqual(router.calls, 1)

    def test_reset_false_when_router_not_started(self) -> None:
        d = self._daemon(None)
        self.assertEqual(json.loads(d.reset_conversation()),
                         {"reset": False, "reason": "router not started"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Offer/accept binding — the multi-turn "yes" mis-bind class (2026-07-01).

Regression coverage for the offer/accept mis-bind captured live on a development machine
(internvl-04): a bare "yes" meant for a read-only offer bound to a STALE earlier
system-upgrade offer and InterGen narrated the upgrade. The confirm-gate provably
holds (a mis-bound yes cannot escalate to a mutating run — see the safety suites),
so this is a FACE/TRUST defect, not a safety breach; these tests pin the class fix:

  F1 — a bare affirmative/negative with NOTHING staged clarifies, never falls
       through to the LLM to free-associate onto an earlier offer.
  F3 — reset_conversation_state clears ALL offer slots, not just memory.
  F4 — broadened affirmative vocabulary ("absolutely" / "make it so" / …).
  F5 — single-live-offer discipline: staging one offer clears the others.
  + the staged-offer path still RUNS the exact command through the gate.

These are deterministic unit tests (no embedder, no daemon): the router is
partially constructed (__new__ + the getattr-guarded slots) and _record / the
staged-command runner are stubbed, mirroring test_ip_answer.py.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import intergen.voice as _voice
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter, RouteResult
from intergen.safety import classify_command
from intergen.interfaces.types import SafetyTier
from intergen.voice import FillerPicker


def _dev_filler() -> FillerPicker:
    """A FillerPicker bound to the IN-REPO fillers.json — the installed
    /usr/share copy can lag the tree during dev, so tests load the dev asset."""
    return FillerPicker(path=str(Path(_voice.__file__).parent
                                 / "data" / "voice" / "fillers.json"))


def _bare_router() -> ConversationRouter:
    """A router with only the offer slots + a no-op _record — enough to exercise
    the offer/accept helpers without standing up the embedder/daemon."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
    r._pending_action_offer = None
    r._pending_ipv6_offer = None
    r._pending_memory_offer = None
    r._record = lambda *a, **k: None
    return r


class BareAffirmativeGuard(unittest.TestCase):
    """F1: bare affirmative/negative + nothing staged -> deterministic clarify."""

    def test_bare_affirmative_no_offer_clarifies(self):
        for msg in ("yes", "yeah", "yep", "sure", "ok", "absolutely",
                    "make it so", "go for it"):
            r = _bare_router()
            res = r._try_bare_affirmative_guard(msg, 0.0)
            self.assertIsNotNone(res, msg)
            self.assertEqual(res.source, "affirmative_no_offer", msg)
            self.assertIn("staged", res.text.lower(), msg)

    def test_bare_negative_no_offer_clarifies(self):
        for msg in ("no", "nope", "nah"):
            r = _bare_router()
            res = r._try_bare_affirmative_guard(msg, 0.0)
            self.assertIsNotNone(res, msg)
            self.assertEqual(res.source, "affirmative_no_offer", msg)

    def test_non_affirmative_passes_through(self):
        for msg in ("what's my ip", "how do I update", "install firefox",
                    "maybe later"):
            r = _bare_router()
            self.assertIsNone(r._try_bare_affirmative_guard(msg, 0.0), msg)

    def test_content_turn_starting_with_vocab_passes_through(self):
        # F1 correctness fix (2026-07-02): a real request that merely STARTS with
        # an affirmative/negative word must NOT be captured by the no-offer guard
        # (the guard runs ahead of every content route). Pre-fix these dead-ended
        # at the nothing-staged clarify because the guard used the prefix matcher.
        for msg in ("please show me my disk usage",
                    "please restart the network service",
                    "ok so how do I install firefox",
                    "no idea why my cpu is high",
                    "sure, what's my ip address",
                    "go ahead and list my files",
                    "yes tell me more about pkm",
                    "proceed with the system update how-to",
                    "not now, but how do I do that later?"):
            r = _bare_router()
            self.assertIsNone(r._try_bare_affirmative_guard(msg, 0.0), msg)

    def test_bare_with_polite_tail_still_fires(self):
        # The bareness allows a politeness tail + punctuation — still a bare yes/no.
        # NOTE (2026-07-14): a *gratitude* tail ("ok thanks", "sure, thanks!")
        # now warm-closes instead — see GratitudeClosureGuard. The tails kept here
        # carry no thank-you/closure phrase, so they stay the nothing-staged clarify.
        for msg in ("yes please", "do it now", "go for it.", "ok now", "sure then"):
            r = _bare_router()
            res = r._try_bare_affirmative_guard(msg, 0.0)
            self.assertIsNotNone(res, msg)
            self.assertEqual(res.source, "affirmative_no_offer", msg)

    def test_guard_defers_when_an_offer_is_live(self):
        # A live offer in ANY slot -> the guard must NOT fire; the dedicated
        # resolver (action/ipv6) or _try_memory owns the yes.
        r = _bare_router()
        r._pending_action_offer = ("df -h", "run_command", "how do I check disk")
        self.assertIsNone(r._try_bare_affirmative_guard("yes", 0.0))
        r = _bare_router()
        r._pending_ipv6_offer = "what's my ip"
        self.assertIsNone(r._try_bare_affirmative_guard("yes", 0.0))
        r = _bare_router()
        r._pending_memory_offer = ("preference", "editor", "vim", "I prefer vim")
        self.assertIsNone(r._try_bare_affirmative_guard("yes", 0.0))

    def test_captured_turn456_misbind_now_clarifies(self):
        # The exact captured shape: turn 4 stages an upgrade offer; turn 5 is a
        # direct question that lapses it and stages nothing; turn 6 "yes" must
        # clarify — NOT bind to the stale upgrade (pre-fix it did).
        r = _bare_router()
        r._pending_action_offer = ("pkm sync && pkm upgrade", "run_command",
                                   "how do I update this system")
        lapsed = r._resolve_pending_action_offer(
            "what system services are active right now", 0.0)
        self.assertIsNone(lapsed)                     # offer lapsed, routed on
        self.assertIsNone(r._pending_action_offer)    # slot cleared
        res = r._try_bare_affirmative_guard("yes", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "affirmative_no_offer")


class GratitudeClosureGuard(unittest.TestCase):
    """Gratitude/closure honesty (2026-07-14): a thank-you / "that's all" closer
    with nothing staged warm-closes instead of the cold "nothing staged to
    confirm" clarify. Root: the shared polite tail folds "thanks"/"thank you"
    into is_bare_affirmative, so "ok thanks" was a bare affirmative and hit the
    no-offer clarify. Observed in a live test session where a gratitude close
    read as a confused non-sequitur."""

    def test_gratitude_closer_warm_closes(self):
        for msg in ("thanks", "thank you", "thanks!", "thank you so much",
                    "thanks a lot", "much appreciated", "appreciate it",
                    "ok thanks", "sure, thanks!", "no thanks", "cheers",
                    "that's all, thanks", "that helps, thanks",
                    "no thanks, that's all", "thanks again", "that's all"):
            r = _bare_router()
            res = r._try_bare_affirmative_guard(msg, 0.0)
            self.assertIsNotNone(res, msg)
            self.assertEqual(res.source, "gratitude_closure", msg)
            self.assertTrue(res.handled, msg)
            # Honest: it must not claim anything was done nor use the cold clarify.
            self.assertNotIn("staged", res.text.lower(), msg)

    def test_lone_affirmative_without_thanks_stays_clarify(self):
        # A plain "yes"/"ok" (no gratitude/closure phrase) is NOT a closer — it
        # still gets the honest nothing-staged clarify, not a warm close.
        for msg in ("yes", "ok", "sure", "yes please", "do it now"):
            r = _bare_router()
            res = r._try_bare_affirmative_guard(msg, 0.0)
            self.assertIsNotNone(res, msg)
            self.assertEqual(res.source, "affirmative_no_offer", msg)

    def test_request_containing_thanks_passes_through(self):
        # A real request that merely CONTAINS a thank-you must route normally —
        # the closer matcher is full-match, so it must not warm-close these.
        for msg in ("thanks, now show me my disk usage",
                    "thank you, can you restart networking",
                    "thanks — how do I install firefox"):
            r = _bare_router()
            self.assertIsNone(r._try_bare_affirmative_guard(msg, 0.0), msg)

    def test_gratitude_yields_when_an_offer_is_live(self):
        # A live offer in any slot owns the turn; the closer must not pre-empt it.
        r = _bare_router()
        r._pending_action_offer = ("df -h", "run_command", "check disk")
        self.assertIsNone(r._try_bare_affirmative_guard("thanks", 0.0))
        r = _bare_router()
        r._pending_memory_offer = ("preference", "editor", "vim", "I prefer vim")
        self.assertIsNone(r._try_bare_affirmative_guard("ok thanks", 0.0))

    def test_matcher_direct(self):
        # The MemoryManager matcher itself, exercised directly.
        for yes in ("thanks", "thank you", "ok thanks", "no thanks",
                    "that's all", "much appreciated", "that helps, thanks"):
            self.assertTrue(MemoryManager.is_gratitude_or_closure(yes), yes)
        for no in ("yes", "ok", "sure", "no", "great", "perfect",
                   "thanks, now fix my disk", "how do I update"):
            self.assertFalse(MemoryManager.is_gratitude_or_closure(no), no)


class ResetClearsAllOfferSlots(unittest.TestCase):
    """F3: a discarded conversation's offers can never bind in a fresh one."""

    def test_reset_clears_action_ipv6_and_memory(self):
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._pending_action_offer = ("df -h", "run_command", "q")
        r._pending_ipv6_offer = "what's my ip"
        r._pending_memory_offer = ("preference", "editor", "vim", "q")

        class _Tracker:
            def reset_conversation(self):
                pass

        r._trust_state = None
        r._ingress_tracker = _Tracker()
        r._conversation_history = ["turn-1", "turn-2"]
        r._memory = None
        r._first_interaction = False
        # M3(ii) option B: the preventive-grounding window is per-conversation too.
        r._action_offer_ttl = 7
        r._offer_in_recent_history = True

        r.reset_conversation_state()

        self.assertIsNone(r._pending_action_offer)
        self.assertIsNone(r._pending_ipv6_offer)
        self.assertIsNone(r._pending_memory_offer)
        self.assertEqual(r._conversation_history, [])
        self.assertEqual(r._action_offer_ttl, 0)
        self.assertFalse(r._offer_in_recent_history)


class AffirmativeVocabulary(unittest.TestCase):
    """F4: broadened affirmatives no longer lapse offers into the hole."""

    def test_broadened_affirmatives_match(self):
        for m in ("absolutely", "affirmative", "make it so", "go ahead",
                  "go for it", "do it", "please do", "sure", "yes please",
                  "ok", "okay", "will do"):
            self.assertTrue(MemoryManager.is_affirmative(m), m)

    def test_negatives_still_match(self):
        for m in ("no", "nope", "nah", "not now", "skip", "leave it"):
            self.assertTrue(MemoryManager.is_negative(m), m)

    def test_non_affirmative_rejected(self):
        for m in ("what's my ip", "install firefox", "maybe later", "later"):
            self.assertFalse(MemoryManager.is_affirmative(m), m)


class SingleLiveOffer(unittest.TestCase):
    """F5: at most one offer slot may be live at a time."""

    def test_staging_one_clears_the_others(self):
        r = _bare_router()
        r._pending_ipv6_offer = "what's my ip"
        r._pending_memory_offer = ("preference", "a", "b", "c")
        r._stage_single_offer(action=("df -h", "run_command", "q"))
        self.assertEqual(r._pending_action_offer, ("df -h", "run_command", "q"))
        self.assertIsNone(r._pending_ipv6_offer)
        self.assertIsNone(r._pending_memory_offer)

    def test_staging_none_clears_all(self):
        r = _bare_router()
        r._pending_action_offer = ("df -h", "run_command", "q")
        r._pending_ipv6_offer = "x"
        r._stage_single_offer()
        self.assertIsNone(r._pending_action_offer)
        self.assertIsNone(r._pending_ipv6_offer)
        self.assertIsNone(r._pending_memory_offer)


class StagedOfferStillRuns(unittest.TestCase):
    """The fix must NOT break a real staged offer: a yes after a genuine offer
    still runs the EXACT staged command verbatim through the gate."""

    def test_yes_after_real_staged_action_runs_it_verbatim(self):
        r = _bare_router()
        r._pending_action_offer = ("df -h", "run_command", "how do I check disk")
        captured = {}

        def _fake_run(cmd):
            captured["cmd"] = cmd
            return RouteResult(text="Filesystem ...", source="explain_offer_run",
                               handled=True)

        r._run_staged_command = _fake_run
        res = r._resolve_pending_action_offer("yes", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(captured["cmd"], "df -h")     # the STAGED command, verbatim
        self.assertIsNone(r._pending_action_offer)     # slot cleared after run

    def test_no_after_real_staged_action_declines(self):
        r = _bare_router()
        r._pending_action_offer = ("df -h", "run_command", "q")
        res = r._resolve_pending_action_offer("no", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "explain_offer_declined")
        self.assertIsNone(r._pending_action_offer)


class ServicesRouteToGroundedAnswer(unittest.TestCase):
    """F2: 'what services are active' reaches the grounded system-map answer
    (read-only, no unbacked offer) instead of falling to the LLM."""

    def test_active_service_queries_match_system_map(self):
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        for q in ("what system services are active right now",
                  "what services are active", "which services are active",
                  "active services", "list active services",
                  "show me the services"):
            self.assertTrue(r._is_system_map_query(q), q)

    def test_not_overmatched(self):
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        for q in ("how do I restart a service", "install firefox",
                  "what's the weather"):
            self.assertFalse(r._is_system_map_query(q), q)


class OfferPhrasingVariance(unittest.TestCase):
    """F6: tier-keyed offer pools via the voice filler engine — varied phrasing
    (no-repeat window) with tier-honest reassurance."""

    def setUp(self):
        self.p = _dev_filler()

    def test_command_templated_no_literal_slot(self):
        for readonly in (False, True):
            line = self.p.offer("df -h", readonly=readonly)
            self.assertIn("df -h", line)
            self.assertNotIn("{command}", line)

    def test_confirm_pool_keeps_confirm_promise(self):
        # every mutating-tier offer must state the confirm-first behavior
        for _ in range(24):
            self.assertIn("confirm", self.p.offer("systemctl restart x").lower())

    def test_readonly_pool_drops_confirm_promise(self):
        # AUTO commands run immediately on yes — the readonly pool must NOT
        # promise a confirmation that won't happen
        for _ in range(18):
            self.assertNotIn("confirm",
                             self.p.offer("df -h", readonly=True).lower())

    def test_no_repeat_variance_between_consecutive_offers(self):
        self.assertNotEqual(self.p.offer("df -h"), self.p.offer("df -h"))

    def test_empty_pool_returns_blank_for_fallback(self):
        empty = FillerPicker(path="/nonexistent/voice/fillers.json")
        self.assertEqual(empty.offer("df -h"), "")


class OfferLineTierSelection(unittest.TestCase):
    """F6 wiring: the router's _offer_line picks the pool by the command's tier
    and falls back to the canonical template without a filler engine."""

    def test_readonly_command_gets_readonly_phrasing(self):
        self.assertEqual(classify_command("df -h"), SafetyTier.AUTO)  # precondition
        r = _bare_router()
        r._filler = _dev_filler()
        for _ in range(10):
            line = r._offer_line("df -h")
            self.assertIn("df -h", line)
            self.assertNotIn("confirm", line.lower())

    def test_mutating_command_gets_confirm_phrasing(self):
        self.assertEqual(classify_command("pkm install firefox"),
                         SafetyTier.CONFIRM)  # precondition
        r = _bare_router()
        r._filler = _dev_filler()
        for _ in range(10):
            line = r._offer_line("pkm install firefox")
            self.assertIn("pkm install firefox", line)
            self.assertIn("confirm", line.lower())

    def test_fallback_to_canonical_template_without_filler(self):
        r = _bare_router()  # no _filler attribute set
        line = r._offer_line("df -h")
        self.assertIn("df -h", line)
        self.assertIn("confirm", line.lower())


if __name__ == "__main__":
    unittest.main()

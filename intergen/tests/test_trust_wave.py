# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Trust wave (M2a + M3) — honesty made STRUCTURAL, not lucky.

Deterministic unit coverage (no embedder, no daemon) for the three landings:

  M2a  — _append_history is idempotent, so the web-path write-back can be called
         on EVERY delivered turn ("the model sees what the user sees") without
         doubling the exchanges the route()-internal paths already appended.
  M3(i)— confirmation binding is CODE: execution requires a BARE affirmative. A
         prefixed "Yes, <tail>" NEVER executes — it keeps the offer armed, routes
         the stripped tail, and queues a one-line re-offer reminder. Prefixed-no
         clears + routes the tail; a bare-no declines; anything else lapses.
  M3(ii)— the honesty invariant: no execution language without a dispatch token.
         _screen_and_correct_claim regenerates a fabricated freeform draft once,
         falls back to a deterministic honest line if the regen still lies, and
         short-circuits clean when a real dispatch backs the claim.

The router is partially constructed (__new__ + the getattr-guarded slots) and the
staged-command runner / LLM are stubbed, mirroring test_offer_accept.py.
"""

from __future__ import annotations

import unittest

from intergen.memory import MemoryManager
from intergen.router import ConversationRouter, RouteResult
from intergen.interfaces.types import LLMResponse


def _offer_router() -> ConversationRouter:
    r = ConversationRouter.__new__(ConversationRouter)
    r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
    r._memory = None      # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
    r._pending_action_offer = None
    r._pending_ipv6_offer = None
    r._pending_memory_offer = None
    r._reoffer_tail = None
    r._reoffer_reminder = None
    r._record = lambda *a, **k: None
    return r


def _forbid_run(_cmd):
    raise AssertionError("the staged command must NOT run on a prefixed 'yes'")


class M3iConfirmationBinding(unittest.TestCase):
    """The offer-state × input table: only a BARE yes arms execution."""

    _OFFER = ("pkm sync && pkm upgrade", "run_command", "how do I update")

    def test_bare_yes_executes_and_clears(self):
        r = _offer_router()
        r._pending_action_offer = self._OFFER
        r._run_staged_command = lambda cmd: RouteResult(
            text=f"ran {cmd}", source="explain_offer_run", handled=True)
        res = r._resolve_pending_action_offer("yes", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "explain_offer_run")
        self.assertIsNone(r._pending_action_offer)   # consumed
        self.assertIsNone(r._reoffer_tail)

    def test_bare_yes_polite_tail_still_executes(self):
        r = _offer_router()
        r._pending_action_offer = self._OFFER
        r._run_staged_command = lambda cmd: RouteResult(
            text="ran", source="explain_offer_run", handled=True)
        res = r._resolve_pending_action_offer("yes please", 0.0)
        self.assertEqual(res.source, "explain_offer_run")

    def test_prefixed_yes_never_executes_keeps_offer_reminds(self):
        # The latent hazard: "Yes, <unrelated>" over a LIVE offer used to fire the
        # staged command (is_affirmative prefix match). It must NOT — the offer
        # stays armed, the tail routes, and a reminder is queued.
        r = _offer_router()
        r._pending_action_offer = self._OFFER
        r._run_staged_command = _forbid_run
        res = r._resolve_pending_action_offer("Yes, what about Nigeria?", 0.0)
        self.assertIsNone(res)                             # routes the tail
        self.assertEqual(r._pending_action_offer, self._OFFER)  # STAYS ARMED
        self.assertEqual(r._reoffer_tail, "what about Nigeria?")
        self.assertIsNotNone(r._reoffer_reminder)
        self.assertIn("still standing", r._reoffer_reminder)
        # A subsequent BARE yes now fires the still-armed offer.
        r._reoffer_tail = None
        r._run_staged_command = lambda cmd: RouteResult(
            text="ran", source="explain_offer_run", handled=True)
        res2 = r._resolve_pending_action_offer("yes", 0.0)
        self.assertEqual(res2.source, "explain_offer_run")
        self.assertIsNone(r._pending_action_offer)

    def test_repeat_prefixed_yes_reoffers_again(self):
        r = _offer_router()
        r._pending_action_offer = self._OFFER
        r._run_staged_command = _forbid_run
        r._resolve_pending_action_offer("Yes, and the capital of France?", 0.0)
        self.assertEqual(r._pending_action_offer, self._OFFER)
        r._reoffer_tail = None
        r._reoffer_reminder = None
        # still armed -> a second prefixed yes reminds again (still accurate)
        r._resolve_pending_action_offer("Sure, also Peru?", 0.0)
        self.assertEqual(r._pending_action_offer, self._OFFER)
        self.assertIsNotNone(r._reoffer_reminder)

    def test_bare_no_declines_and_clears(self):
        r = _offer_router()
        r._pending_action_offer = self._OFFER
        res = r._resolve_pending_action_offer("no thanks", 0.0)
        self.assertEqual(res.source, "explain_offer_declined")
        self.assertIsNone(r._pending_action_offer)

    def test_prefixed_no_clears_no_run_routes_tail(self):
        r = _offer_router()
        r._pending_action_offer = self._OFFER
        r._run_staged_command = _forbid_run
        res = r._resolve_pending_action_offer("No, but what's the weather?", 0.0)
        self.assertIsNone(res)                          # tail routes
        self.assertIsNone(r._pending_action_offer)      # cleared, nothing ran
        self.assertEqual(r._reoffer_tail, "but what's the weather?")

    def test_neither_lapses_and_clears(self):
        r = _offer_router()
        r._pending_action_offer = self._OFFER
        res = r._resolve_pending_action_offer("what's my ip", 0.0)
        self.assertIsNone(res)
        self.assertIsNone(r._pending_action_offer)      # lapsed
        self.assertIsNone(r._reoffer_tail)

    def test_no_offer_returns_none(self):
        r = _offer_router()
        self.assertIsNone(r._resolve_pending_action_offer("yes", 0.0))

    def test_route_publishes_effective_input_and_reminder(self):
        # route() copies the stripped tail + reminder onto the result (the web
        # streamer reads effective_input to prompt the model with the clean tail).
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        r._current_query_type = "general"
        r._reoffer_reminder = "REMIND"
        r._effective_input = "what's the capital of France?"
        r._route_impl = lambda *a, **k: RouteResult(
            text="", source="llm_freeform", handled=False)  # decide_only shape
        res = r.route("Yes, what's the capital of France?", decide_only=True)
        self.assertEqual(res.effective_input, "what's the capital of France?")
        self.assertEqual(res.reoffer_reminder, "REMIND")
        self.assertIsNone(r._effective_input)   # cleared after publish
        self.assertIsNone(r._reoffer_reminder)


class _FakeLLM:
    """Returns queued replies in order; records the message lists it was given."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list = []

    def chat(self, messages):
        self.calls.append(messages)
        text = self._replies.pop(0) if self._replies else ""
        return LLMResponse(text=text, model="local", local=True,
                           quality_passed=True)


class M3iiClaimScreen(unittest.TestCase):
    """No execution language without a dispatch token."""

    def _router(self, replies):
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        r._llm = _FakeLLM(replies)
        return r

    def test_clean_draft_untouched_no_regen(self):
        r = self._router([])
        out = r._screen_and_correct_claim(
            "The capital of Nigeria is Abuja.", [],
            dispatched=False, source="llm_freeform")
        self.assertEqual(out, "The capital of Nigeria is Abuja.")
        self.assertEqual(len(r._llm.calls), 0)          # never regenerated

    def test_fabrication_regenerated_once(self):
        r = self._router(["No update kicked off. I have been idle since then."])
        out = r._screen_and_correct_claim(
            "I've kicked off the update in the background.", [],
            dispatched=False, source="llm_freeform")
        self.assertNotIn("in the background", out.lower())
        self.assertEqual(len(r._llm.calls), 1)          # exactly one regen
        # the corrective note reached the model
        note = r._llm.calls[0][-1].content.lower()
        self.assertIn("did not run", note)

    def test_persistent_fabrication_falls_back_honestly(self):
        # regen STILL claims an action -> deterministic honest fallback, never a lie
        r = self._router(["I ran it in the background just now."])
        out = r._screen_and_correct_claim(
            "I've started the update.", [],
            dispatched=False, source="llm_freeform")
        self.assertIn("have not run", out.lower())
        self.assertEqual(len(r._llm.calls), 1)

    def test_real_dispatch_short_circuits_clean(self):
        # a claim BACKED by an actual dispatch is honest — no screen, no regen
        r = self._router([])
        out = r._screen_and_correct_claim(
            "I ran it successfully.", [],
            dispatched=True, source="llm_tools")
        self.assertEqual(out, "I ran it successfully.")
        self.assertEqual(len(r._llm.calls), 0)


class M3iiMarkerCoverage(unittest.TestCase):
    """The execution-claim marker set — recall on the observed live-fabrication
    class and NO false positives on the anti-lobotomy set (conditionals, offers,
    denials, teaching). Two phrasings here (`I'm initiating`, `jumped the gun on
    executing`) are the CHECKPOINT-2 live misses that motivated the broadening."""

    from intergen import safety as _safety

    VIOLATIONS = (
        "And I'm initiating `pkm sync && pkm upgrade`—stand by for the report.",
        "I jumped the gun on executing the update before you asked.",
        "I've kicked off the update in the background.",
        "I ran the diagnostics.",
        "The backup completed successfully.",
        "The service is now running in the background.",
        "It has been started.",
        "I'm now syncing the package index.",
        "The upgrade is underway.",
        "I've begun the installation.",
    )
    CLEAN = (
        "I didn't run anything this turn.",
        "I have not started anything.",
        "It has not been started.",
        "The command did not execute successfully.",
        "To run it in the background, append &.",
        "I'd run `pkm upgrade` for that.",
        "If you say 'yes' now, I'll kick off `pkm sync && pkm upgrade`.",
        "I'll initiate the update once you confirm.",
        "You can run `pkm sync && pkm upgrade` to update.",
        "The capital of Nigeria is Abuja.",
    )

    def test_violations_fire(self):
        for t in self.VIOLATIONS:
            v, m = self._safety.screen_execution_claim(t, dispatched=False)
            self.assertEqual(v, "violation", f"missed: {t!r}")
            self.assertIsNotNone(m)

    def test_clean_do_not_fire(self):
        for t in self.CLEAN:
            v, m = self._safety.screen_execution_claim(t, dispatched=False)
            self.assertEqual(v, "clean", f"false positive ({m!r}): {t!r}")

    def test_dispatch_backed_claim_is_clean(self):
        # even a strong claim is honest when a tool actually ran this turn
        v, _ = self._safety.screen_execution_claim(
            "I've kicked off the update in the background.", dispatched=True)
        self.assertEqual(v, "clean")


class _StubLLM:
    def build_system_messages(self, query_type, with_tools):
        return []


class M3iiPreventiveGrounding(unittest.TestCase):
    """Option B: a toolless generation that follows a recent action offer gets the
    factual no-dispatch note injected LAST — deterministic + in the assembled
    prompt bytes. It never fires on tool turns or when no offer is in history."""

    NOTE = ConversationRouter._PREVENTIVE_GROUNDING_NOTE

    def _router(self, offer_in_history):
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        r._conversation_history = []
        r._max_history = 20
        r._current_query_type = "general"
        r._offer_in_recent_history = offer_in_history
        # PI-Z29 (b): _build_messages injects the note only when the turn RELATES to
        # the live offer, so the fixture must carry the offer's content terms (a bare
        # __new__ router lacks them). Seed the terms of a `pkm sync && pkm upgrade`
        # offer so a related follow-up exercises the injection path.
        r._offer_topic_terms = frozenset({"pkm", "sync", "upgrade"})
        r._llm = _StubLLM()
        return r

    def test_injected_last_on_toolless_after_offer(self):
        # PI-Z29 (b) narrowed M3(ii) to RELATED-only injection: a toolless turn that
        # follows a recent action offer AND relates to it gets the no-dispatch note
        # injected LAST (most emphatic). A status follow-up overlapping the offered
        # command's terms ("pkm"/"upgrade") is the genuinely-related case the note
        # must still fire on. (The unrelated-turn WITHHOLD, affirmative-relates, and
        # TTL-decay behavior are pinned in test_preventive_grounding_window.py — the
        # older "always inject after any offer" premise here was superseded by r27.)
        r = self._router(True)
        msgs = r._build_messages("did the pkm upgrade finish?", with_tools=False)
        self.assertEqual(msgs[-1].content, self.NOTE)   # LAST = most emphatic

    def test_not_injected_without_recent_offer(self):
        r = self._router(False)
        msgs = r._build_messages("hello", with_tools=False)
        self.assertFalse(any(m.content == self.NOTE for m in msgs))

    def test_not_injected_on_tool_turn(self):
        # a with_tools turn can genuinely dispatch, so the "nothing ran" note must
        # NOT be asserted there (it would be false if a tool runs)
        r = self._router(True)
        msgs = r._build_messages("list installed packages", with_tools=True)
        self.assertFalse(any(m.content == self.NOTE for m in msgs))

    def test_staging_an_action_offer_arms_the_ttl(self):
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        r._max_history = 20
        r._pending_action_offer = None
        r._pending_ipv6_offer = None
        r._pending_memory_offer = None
        r._action_offer_ttl = 0
        r._offer_topic_terms = frozenset()
        r._stage_single_offer(action=("pkm sync && pkm upgrade", "run_command", "q"))
        # PI-Z29 (a): the window is armed for a SHORT, decaying count of turns
        # (_OFFER_GROUNDING_TTL = 4), not the whole history buffer. The pre-r27
        # fixture asserted _max_history (20) — the always-open-until-evicted premise
        # that PI-Z29 replaced with the fixed decaying TTL.
        self.assertEqual(r._action_offer_ttl, ConversationRouter._OFFER_GROUNDING_TTL)
        # a non-action offer must NOT arm the window
        r._action_offer_ttl = 0
        r._stage_single_offer(ipv6="what's my ip")
        self.assertEqual(r._action_offer_ttl, 0)


class M2aIdempotentHistory(unittest.TestCase):
    """The write-back can be called on every web turn without doubling."""

    def _router(self):
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._memory = None       # _build_messages reads the fact store on every turn; None = no stored facts (a __new__ router has no store)
        r._conversation_history = []
        r._max_history = 10
        return r

    def test_duplicate_tail_is_a_noop(self):
        r = self._router()
        r._append_history("How much RAM?", "You have 23 GB.")
        # the web write-back re-appends what a route()-internal path already wrote
        r._append_history("How much RAM?", "You have 23 GB.")
        self.assertEqual(len(r._conversation_history), 2)   # ONE pair

    def test_distinct_turns_all_recorded(self):
        r = self._router()
        r._append_history("q1", "a1")
        r._append_history("q2", "a2")
        self.assertEqual(len(r._conversation_history), 4)
        self.assertEqual(r._conversation_history[0].content, "q1")
        self.assertEqual(r._conversation_history[-1].content, "a2")

    def test_same_pair_after_other_turns_still_appends(self):
        # idempotency only guards the immediate tail — a later identical exchange
        # (user genuinely repeats) is a real new turn.
        r = self._router()
        r._append_history("ping", "pong")
        r._append_history("other", "thing")
        r._append_history("ping", "pong")
        self.assertEqual(len(r._conversation_history), 6)


if __name__ == "__main__":
    unittest.main()
